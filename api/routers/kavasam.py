"""Digital Kavasam — real YouTube channel health API.

Primary data path is the YouTube Data API v3; when no key is configured it falls
back to public-page scraping. Both feed the same channel_snapshots table. The
heavy scan runs in the agents venv (requests + bs4 + googleapiclient) via a
subprocess bridge, mirroring the rest of the platform.

CRITICAL anomalies (e.g. mass video deletion) trigger a CERT-In *draft* only —
never auto-filed. A human reviews and files it manually.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import Channel, engine
from models.snapshot import ChannelSnapshot

_AGENTS_PYTHON = Path(__file__).resolve().parents[2] / "agents" / ".venv" / "bin" / "python"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

router = APIRouter(prefix="/api/kavasam", tags=["kavasam"])

_SLUGS = ("brand_main", "brand_main", "brand_news", "brand_entertainment", "brand_movies")


# ── helpers ───────────────────────────────────────────────
def _kavasam_config(db: Session) -> dict:
    rows = db.execute(
        text("SELECT key, value FROM platform_config WHERE module = 'kavasam'")
    ).fetchall()
    return {k: v for k, v in rows}


def _resolve_api_key(cfg: dict) -> str | None:
    """platform_config → root .env → process env (matches the scan's resolution)."""
    if cfg.get("youtube_api_key"):
        return cfg["youtube_api_key"]
    root_env = dotenv_values(_PROJECT_ROOT / ".env")
    return root_env.get("YOUTUBE_API_KEY") or os.getenv("YOUTUBE_API_KEY") or None


def _run_scan() -> dict:
    script = (
        "import json; from agents.kavasam.youtube_client import run_scan; "
        "print(json.dumps(run_scan()))"
    )
    result = subprocess.run(
        [str(_AGENTS_PYTHON), "-c", script],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=150,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:500])
    return json.loads(result.stdout.strip().splitlines()[-1])


# Example Broadcaster's Spiritual (spiritual programming) sub-channel shares the "Example Broadcaster"
# display name with the main channel — disambiguate by channel_id.
_SPIRITUAL_CHANNEL_ID = "UC_EXAMPLE_CHANNEL_ID"


def _display_channel_name(name: str, channel_id: str) -> str:
    if channel_id == _SPIRITUAL_CHANNEL_ID and not (name or "").endswith("(Spiritual)"):
        return f"{name} (Spiritual)"
    return name


def _overall_status(channels: list[dict]) -> str:
    statuses = {c.get("status") for c in channels}
    if "CRITICAL" in statuses:
        return "CRITICAL"
    if "ANOMALY" in statuses:
        return "DEGRADED"
    return "SECURE"


def _channels_from_db() -> list[dict]:
    """Fallback view built from the latest persisted snapshot per channel."""
    out: list[dict] = []
    with Session(engine) as db:
        channels = db.execute(select(Channel)).scalars().all()
        for ch in channels:
            latest = db.execute(
                select(ChannelSnapshot)
                .where(ChannelSnapshot.channel_id == ch.id)
                .order_by(ChannelSnapshot.scanned_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            anomalies = []
            if latest and latest.anomaly_detected:
                anomalies = (latest.judge or {}).get("anomalies", []) if isinstance(latest.judge, dict) else []
                if not anomalies and latest.anomaly_type:
                    anomalies = [{"anomaly_type": latest.anomaly_type, "detail": latest.anomaly_detail}]
            ch_status = (
                "CRITICAL" if latest and latest.anomaly_type == "VIDEO_COUNT_DROP"
                else "ANOMALY" if latest and latest.anomaly_detected
                else "SECURE"
            )
            out.append({
                "channel_name": _display_channel_name(ch.name, ch.channel_id),
                "subscriber_count": latest.subscriber_count if latest else None,
                "video_count": latest.video_count if latest else None,
                "status": ch_status,
                "data_source": "public_scrape",
                "last_updated": latest.scanned_at.isoformat() if latest and latest.scanned_at else None,
                "anomalies": anomalies,
            })
    return out


# ── GET /api/kavasam/channels ─────────────────────────────
@router.get("/channels")
def list_channels():
    with Session(engine) as db:
        cfg = _kavasam_config(db)
    api_configured = bool(_resolve_api_key(cfg))

    try:
        scan = _run_scan()
        channels = scan.get("channels", [])
        api_configured = scan.get("api_configured", api_configured)
    except Exception:
        # Degrade gracefully to the last persisted snapshots.
        channels = _channels_from_db()

    return {
        "channels": channels,
        "overall_status": _overall_status(channels),
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "api_configured": api_configured,
    }


# ── POST /api/kavasam/scan ────────────────────────────────
@router.post("/scan", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
def trigger_scan(request: Request):
    try:
        scan = _run_scan()
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Scan timed out.")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    scan["overall_status"] = _overall_status(scan.get("channels", []))
    scan["last_scan"] = datetime.now(timezone.utc).isoformat()
    return scan


# ── GET /api/kavasam/anomalies ────────────────────────────
@router.get("/anomalies")
def list_anomalies():
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with Session(engine) as db:
        rows = db.execute(
            select(ChannelSnapshot, Channel)
            .join(Channel, ChannelSnapshot.channel_id == Channel.id)
            .where(ChannelSnapshot.anomaly_detected.is_(True))
            .where(ChannelSnapshot.scanned_at >= since)
            .order_by(ChannelSnapshot.scanned_at.desc())
            .limit(50)
        ).all()

        result = []
        for snap, ch in rows:
            cert_id = None
            if isinstance(snap.judge, dict):
                cert_id = snap.judge.get("cert_in_draft_id")
            result.append({
                "snapshot_id": str(snap.id),
                "channel_id": ch.channel_id,
                "channel_name": _display_channel_name(ch.name, ch.channel_id),
                "platform": ch.platform,
                "anomaly_type": snap.anomaly_type,
                "anomaly_detail": snap.anomaly_detail,
                "subscriber_count": snap.subscriber_count,
                "video_count": snap.video_count,
                "scanned_at": snap.scanned_at.isoformat() if snap.scanned_at else None,
                "cert_in_draft_id": cert_id,
            })
    return {"anomalies": result, "total": len(result)}


# ── GET /api/kavasam/metrics ──────────────────────────────
@router.get("/metrics")
def get_metrics():
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with Session(engine) as db:
        cfg = _kavasam_config(db)
        api_configured = bool(_resolve_api_key(cfg))

        # Only channels with a real channel_id are actually monitored — rows
        # still holding a setup placeholder are skipped by the scanners.
        channels_watched = db.execute(
            text(
                "SELECT count(*) FROM channels "
                "WHERE status = 'active' "
                "  AND coalesce(trim(channel_id), '') NOT IN ('', 'PASTE_ID_HERE')"
            )
        ).scalar_one()

        anomalies_24h = db.execute(
            select(func.count()).select_from(ChannelSnapshot)
            .where(ChannelSnapshot.anomaly_detected.is_(True))
            .where(ChannelSnapshot.scanned_at >= since)
        ).scalar_one()

        cert_in_drafts = db.execute(
            text("SELECT COUNT(*) FROM cert_in_drafts")
        ).scalar_one()

        oldest_audit = db.execute(
            text("SELECT MIN(created_at) FROM audit_log")
        ).scalar()

        severity_rows = db.execute(
            select(ChannelSnapshot.anomaly_type, func.count())
            .where(ChannelSnapshot.anomaly_detected.is_(True))
            .where(ChannelSnapshot.scanned_at >= since)
            .group_by(ChannelSnapshot.anomaly_type)
        ).all()

    audit_retained_days = 0
    if oldest_audit is not None:
        if oldest_audit.tzinfo is None:
            oldest_audit = oldest_audit.replace(tzinfo=timezone.utc)
        audit_retained_days = (datetime.now(timezone.utc) - oldest_audit).days

    anomalies_by_severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    # anomaly_type vocabulary — both detectors (youtube_client.detect_anomalies
    # and channel_health_agent._detect_anomaly) now emit these type names.
    # channel_health_agent previously wrote bare severity strings ("CRITICAL",
    # "HIGH") into this column, which this map could not resolve.
    _SEV = {
        "VIDEO_COUNT_DROP": "CRITICAL", "SUBSCRIBER_DROP": "HIGH",
        "CHANNEL_STATUS_CHANGE": "HIGH", "SUBSCRIBER_SPIKE": "MEDIUM",
        "VIDEO_COUNT_SPIKE": "LOW", "SCAN_ERROR": "MEDIUM",
    }
    for anomaly_type, count in severity_rows:
        sev = _SEV.get(anomaly_type or "", anomaly_type)
        if sev in anomalies_by_severity:
            anomalies_by_severity[sev] += count

    return {
        "channels_watched": channels_watched,
        "anomalies_24h": anomalies_24h,
        "cert_in_drafts": cert_in_drafts,
        # Real retained span of the audit log. Retention is enforced by WORM
        # immutability (no delete path exists), not by a 180-day policy job —
        # this previously reported the policy number 180 regardless of what was
        # actually on record.
        "audit_retained_days": audit_retained_days,
        "audit_retention_basis": "WORM trigger blocks DELETE/UPDATE",
        "data_source": "youtube_api" if api_configured else "public_scrape",
        "api_configured": api_configured,
        "anomalies_by_severity": anomalies_by_severity,
    }


# ── POST /api/kavasam/config ──────────────────────────────
class ChannelIds(BaseModel):
    brand_main: str = ""
    brand_main: str = ""
    brand_news: str = ""
    brand_entertainment: str = ""
    brand_movies: str = ""


class KavasamConfigRequest(BaseModel):
    youtube_api_key: str = ""
    channel_ids: ChannelIds = ChannelIds()


@router.post("/config")
def save_config(body: KavasamConfigRequest):
    pairs = {"youtube_api_key": body.youtube_api_key}
    for slug in _SLUGS:
        val = getattr(body.channel_ids, slug, "")
        if val:
            pairs[f"channel_id_{slug}"] = val
    with Session(engine) as db:
        for key, value in pairs.items():
            db.execute(
                text(
                    "INSERT INTO platform_config (module, key, value, updated_at) "
                    "VALUES ('kavasam', :k, :v, NOW()) "
                    "ON CONFLICT (module, key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = NOW()"
                ),
                {"k": key, "v": value},
            )
        db.commit()
    return {"saved": True}
