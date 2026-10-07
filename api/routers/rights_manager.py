"""Rights Manager & Strike Tracker API.

Manage broadcast reference clips and copyright strike drafts against Meta
platforms.

HUMAN-IN-THE-LOOP: strikes are created as DRAFT only. Sentinel never
auto-files a strike — a human reviews the draft and files it manually through
Meta's Rights Manager. The lifecycle mirrors the Content Protection DMCA drafter:
DETECTED → confirm → draft → file manually.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.channel import engine

router = APIRouter(prefix="/api/rights-manager", tags=["rights-manager"])

_STORAGE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference_files"
_STRIKE_STATUSES = ("DRAFT", "FILED", "CONFIRMED", "REJECTED")


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


# ── POST /api/rights-manager/upload-reference ─────────────
@router.post("/upload-reference")
async def upload_reference(
    file: UploadFile = File(...),
    title: str = Form(...),
    channel: str = Form(...),
    broadcast_date: str = Form(None),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix or ".bin"
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = _STORAGE_DIR / stored_name
    stored_path.write_bytes(data)

    bdate: date | None = None
    if broadcast_date:
        try:
            bdate = date.fromisoformat(broadcast_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="broadcast_date must be YYYY-MM-DD")

    with Session(engine) as db:
        row = db.execute(
            text(
                "INSERT INTO reference_files (title, channel, file_path, broadcast_date) "
                "VALUES (:t, :c, :p, :d) RETURNING id"
            ),
            {"t": title, "c": channel, "p": str(stored_path), "d": bdate},
        ).first()
        db.commit()

    return {"id": str(row[0]), "title": title, "status": "uploaded"}


# ── GET /api/rights-manager/reference-files ───────────────
@router.get("/reference-files")
def list_reference_files():
    with Session(engine) as db:
        rows = db.execute(
            text(
                "SELECT id, title, channel, file_path, broadcast_date, "
                "submitted_to_meta, created_at "
                "FROM reference_files ORDER BY created_at DESC"
            )
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "title": r[1],
            "channel": r[2],
            "file_path": r[3],
            "broadcast_date": _iso(r[4]),
            "submitted_to_meta": bool(r[5]),
            "status": "SUBMITTED" if r[5] else "UPLOADED",
            "created_at": _iso(r[6]),
        }
        for r in rows
    ]


# ── POST /api/rights-manager/strikes ──────────────────────
class StrikeRequest(BaseModel):
    platform: str
    content_url: str
    reference_file_id: str | None = None
    notes: str | None = None


@router.post("/strikes")
def create_strike(body: StrikeRequest):
    """Create a strike DRAFT. Never auto-filed — a human files it manually."""
    if not body.platform.strip() or not body.content_url.strip():
        raise HTTPException(status_code=400, detail="platform and content_url are required.")

    with Session(engine) as db:
        row = db.execute(
            text(
                "INSERT INTO meta_strikes (platform, content_url, reference_file_id, notes, status) "
                "VALUES (:p, :u, :ref, :n, 'DRAFT') RETURNING id"
            ),
            {
                "p": body.platform,
                "u": body.content_url,
                "ref": body.reference_file_id or None,
                "n": body.notes,
            },
        ).first()
        db.commit()

    return {
        "strike_id": str(row[0]),
        "status": "DRAFT",
        "message": "Strike draft created — review and file manually",
    }


# ── GET /api/rights-manager/strikes ───────────────────────
@router.get("/strikes")
def list_strikes():
    with Session(engine) as db:
        rows = db.execute(
            text(
                "SELECT id, platform, content_url, reference_file_id, notes, "
                "status, filed_at, created_at "
                "FROM meta_strikes ORDER BY created_at DESC"
            )
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "platform": r[1],
            "content_url": r[2],
            "reference_file_id": str(r[3]) if r[3] else None,
            "notes": r[4],
            "status": r[5],
            "filed_at": _iso(r[6]),
            "created_at": _iso(r[7]),
        }
        for r in rows
    ]
