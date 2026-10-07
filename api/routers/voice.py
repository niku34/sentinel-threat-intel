"""Anchor voice fingerprinting API.

Enroll anchor voice profiles, verify uploaded audio/video against them, and
trigger monitoring sweeps.

Cross-venv note
---------------
Feature extraction needs librosa, which only installs on the agents venv
(Python 3.11). This API runs on Python 3.14, so extraction is delegated to the
agents venv via subprocess (the fingerprinter's CLI). Comparison is numpy-only,
so it runs in-process here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import engine

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agents.voice.fingerprinter import VoiceFingerprinter  # noqa: E402

router = APIRouter(prefix="/api/voice", tags=["voice"])

_VF = VoiceFingerprinter()  # used for compare() / (de)serialise only
_AGENTS_PYTHON = _PROJECT_ROOT / "agents" / ".venv" / "bin" / "python"

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus"}
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

SAME_SPEAKER_THRESHOLD = 0.85
CLONE_RISK_LOW = 0.60


def _detect_kind(content_type: str, filename: str) -> str:
    ct = (content_type or "").lower()
    ext = Path(filename or "").suffix.lower()
    if ct.startswith("video/") or ext in _VIDEO_EXTS:
        return "video"
    if ct.startswith("audio/") or ext in _AUDIO_EXTS:
        return "audio"
    # webm can be either; default to audio for ambiguous cases.
    return "audio"


def _extract_fingerprint(data: bytes, kind: str, suffix: str) -> dict:
    """Delegate feature extraction to the agents venv (librosa) via subprocess."""
    mode = "extract-video" if kind == "video" else "extract"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PROJECT_ROOT)
    # Ensure a usable ffmpeg is discoverable for video extraction.
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"

    tmp = tempfile.NamedTemporaryFile(suffix=suffix or ".bin", delete=False)
    try:
        tmp.write(data)
        tmp.flush()
        tmp.close()
        proc = subprocess.run(
            [str(_AGENTS_PYTHON), "-m", "agents.voice.fingerprinter", mode, tmp.name],
            cwd=str(_PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            raise HTTPException(
                status_code=422,
                detail=f"Fingerprint extraction failed: {proc.stderr.strip()[:400] or proc.stdout.strip()[:400]}",
            )
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise HTTPException(status_code=422, detail="Extractor returned invalid JSON.")
        if "error" in result:
            raise HTTPException(status_code=422, detail=f"Extractor error: {result['error']}")
        return result
    finally:
        if os.path.exists(tmp.name):
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── POST /api/voice/enroll ────────────────────────────────
@router.post("/enroll")
@limiter.limit("20/minute")
async def enroll(
    request: Request,
    file: UploadFile = File(...),
    anchor_name: str = Form(...),
    channel: str = Form(...),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    kind = _detect_kind(file.content_type or "", file.filename or "")
    suffix = Path(file.filename or "").suffix or (".mp4" if kind == "video" else ".mp3")

    fp = _extract_fingerprint(data, kind, suffix)
    fp_str = _VF.fingerprint_to_string(fp)
    duration = fp.get("duration_seconds")

    with Session(engine) as db:
        row = db.execute(
            text(
                "INSERT INTO anchor_voice_profiles "
                "(anchor_name, channel, fingerprint_data, sample_duration_seconds, audio_source, file_type) "
                "VALUES (:n, :c, :f, :d, :s, :t) "
                "RETURNING id, created_at"
            ),
            {
                "n": anchor_name,
                "c": channel,
                "f": fp_str,
                "d": duration,
                "s": f"upload:{file.filename}",
                "t": kind,
            },
        ).first()
        db.commit()

    return {
        "id": str(row[0]),
        "anchor_name": anchor_name,
        "channel": channel,
        "duration_seconds": duration,
        "file_type": kind,
        "enrolled_at": _iso(row[1]),
    }


# ── POST /api/voice/verify ────────────────────────────────
@router.post("/verify")
@limiter.limit("20/minute")
async def verify(
    request: Request,
    file: UploadFile = File(...),
    anchor_name: str = Form(None),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    kind = _detect_kind(file.content_type or "", file.filename or "")
    suffix = Path(file.filename or "").suffix or (".mp4" if kind == "video" else ".mp3")

    query_fp = _extract_fingerprint(data, kind, suffix)

    with Session(engine) as db:
        sql = "SELECT anchor_name, channel, fingerprint_data FROM anchor_voice_profiles"
        params: dict = {}
        if anchor_name:
            sql += " WHERE anchor_name = :n"
            params["n"] = anchor_name
        profiles = db.execute(text(sql), params).fetchall()

    comparisons = []
    for name, chan, fp_str in profiles:
        try:
            stored = _VF.string_to_fingerprint(fp_str)
            sim = _VF.compare(query_fp, stored)
        except Exception:  # noqa: BLE001
            continue
        comparisons.append({"anchor_name": name, "channel": chan, "similarity": sim})

    comparisons.sort(key=lambda c: c["similarity"], reverse=True)
    best = comparisons[0] if comparisons else None
    best_sim = best["similarity"] if best else 0.0

    is_same = best_sim >= SAME_SPEAKER_THRESHOLD
    is_clone = CLONE_RISK_LOW <= best_sim < SAME_SPEAKER_THRESHOLD

    return {
        "match_found": is_same,
        "matched_anchor": best["anchor_name"] if (best and is_same) else None,
        "similarity": best_sim,
        "is_clone_risk": is_clone,
        "is_same_speaker": is_same,
        "all_comparisons": comparisons,
    }


# ── GET /api/voice/profiles ───────────────────────────────
@router.get("/profiles")
def list_profiles():
    with Session(engine) as db:
        rows = db.execute(
            text(
                "SELECT id, anchor_name, channel, sample_duration_seconds, "
                "file_type, audio_source, created_at "
                "FROM anchor_voice_profiles ORDER BY created_at DESC"
            )
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "anchor_name": r[1],
            "channel": r[2],
            "sample_duration_seconds": r[3],
            "file_type": r[4],
            "audio_source": r[5],
            "created_at": _iso(r[6]),
        }
        for r in rows
    ]


# ── DELETE /api/voice/profiles/{id} ───────────────────────
@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str):
    with Session(engine) as db:
        res = db.execute(
            text("DELETE FROM anchor_voice_profiles WHERE id = :id"),
            {"id": profile_id},
        )
        db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return {"deleted": True, "id": profile_id}


# ── POST /api/voice/monitor/sweep ─────────────────────────
@router.post("/monitor/sweep")
@limiter.limit("6/minute")
def monitor_sweep(request: Request):
    """Run VoiceMonitor.run_monitoring_sweep() in the agents venv (yt-dlp/librosa)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PROJECT_ROOT)
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"
    try:
        proc = subprocess.run(
            [str(_AGENTS_PYTHON), "-m", "agents.voice.monitor", "sweep"],
            cwd=str(_PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Monitoring sweep timed out.")
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Sweep failed: {proc.stderr.strip()[:400]}",
        )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise HTTPException(status_code=500, detail="Sweep returned invalid JSON.")
