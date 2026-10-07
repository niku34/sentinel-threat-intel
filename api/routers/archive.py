"""Archive fingerprinting API.

Perceptual-hash an uploaded/linked clip and search it against the
archive_fingerprints table to detect reused Example Broadcaster footage.

The heavy lifting lives in agents/archive/fingerprinter.py. Its only deps are
imagehash + Pillow, which are installed in this API venv too, so we import it
directly (project root added to sys.path) rather than crossing a venv boundary.
"""
from __future__ import annotations

import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import engine

# Make `agents.archive.fingerprinter` importable from the API process.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agents.archive.fingerprinter import ArchiveFingerprinter  # noqa: E402

router = APIRouter(prefix="/api/archive", tags=["archive"])

_FP = ArchiveFingerprinter()

# platform_config coordinates (module, key) for the archive storage path.
ARCHIVE_MODULE = "archive"
ARCHIVE_PATH_KEY = "archive_storage_path"
DEFAULT_STORAGE_PATH = "/mnt/archive (not configured)"

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


# ── platform_config helpers (shared with admin router) ────
def get_archive_path(db: Session) -> str | None:
    row = db.execute(
        text(
            "SELECT value FROM platform_config "
            "WHERE module = :m AND key = :k"
        ),
        {"m": ARCHIVE_MODULE, "k": ARCHIVE_PATH_KEY},
    ).first()
    return row[0] if row else None


def set_archive_path(db: Session, path: str) -> None:
    db.execute(
        text(
            "INSERT INTO platform_config (module, key, value, updated_at) "
            "VALUES (:m, :k, :v, NOW()) "
            "ON CONFLICT (module, key) DO UPDATE "
            "SET value = EXCLUDED.value, updated_at = NOW()"
        ),
        {"m": ARCHIVE_MODULE, "k": ARCHIVE_PATH_KEY, "v": path},
    )


def _load_db_hashes(db: Session) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT clip_id, fingerprint_hash, fingerprint_type "
            "FROM archive_fingerprints"
        )
    ).fetchall()
    return [
        {
            "clip_id": str(r[0]),
            "fingerprint_hash": r[1],
            "fingerprint_type": r[2],
        }
        for r in rows
    ]


# ── models ────────────────────────────────────────────────
class ClipUrlRequest(BaseModel):
    clip_url: str


class IndexRequest(BaseModel):
    storage_path: str


# ── POST /api/archive/fingerprint ─────────────────────────
@router.post("/fingerprint")
@limiter.limit("30/minute")
async def fingerprint(request: Request):
    """Fingerprint an uploaded file (multipart) or a linked clip ({clip_url})."""
    content_type = request.headers.get("content-type", "")
    data: bytes
    media_type = "image"

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="No 'file' in upload.")
        data = await upload.read()
        media_type = getattr(upload, "content_type", None) or "image"
    else:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Send a multipart 'file' upload or JSON {clip_url}.",
            )
        clip_url = (payload or {}).get("clip_url")
        if not clip_url:
            raise HTTPException(status_code=400, detail="clip_url is required.")
        try:
            req = urllib.request.Request(clip_url, headers={"User-Agent": "Sentinel/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                data = resp.read()
                media_type = resp.headers.get("Content-Type", "") or _guess_media(clip_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"Could not fetch clip_url: {exc}",
            )

    try:
        query_hash = _FP.hash_from_bytes(data, media_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not fingerprint media: {exc}")

    with Session(engine) as db:
        db_hashes = _load_db_hashes(db)

    matches = _FP.search_archive(query_hash, db_hashes)
    highest = matches[0]["similarity"] if matches else 0.0

    return {
        "query_hash": query_hash,
        "matches": matches,
        "match_found": bool(matches),
        "highest_similarity": highest,
    }


def _guess_media(name: str) -> str:
    ext = Path(name.split("?")[0]).suffix.lower()
    if ext in _VIDEO_EXTS:
        return "video"
    return "image"


# ── GET /api/archive/stats ────────────────────────────────
@router.get("/stats")
def stats():
    with Session(engine) as db:
        total = db.execute(
            text("SELECT COUNT(*) FROM archive_fingerprints")
        ).scalar_one()
        clips = db.execute(
            text("SELECT COUNT(*) FROM archive_clips")
        ).scalar_one()
        last = db.execute(
            text("SELECT MAX(created_at) FROM archive_fingerprints")
        ).scalar_one()
        path = get_archive_path(db) or DEFAULT_STORAGE_PATH

    last_iso = None
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        last_iso = last.isoformat()

    return {
        "total_fingerprints": int(total),
        "total_clips": int(clips),
        "storage_path": path,
        "last_indexed": last_iso,
        "archive_connected": bool(total),
        "note": (
            "Awaiting real archive connection — no Example Broadcaster footage has been indexed yet. "
            "Fingerprint matching returns no matches until the archive is mounted and indexed."
            if not total else
            f"{total} fingerprint(s) indexed from {clips} clip(s)."
        ),
    }


# ── POST /api/archive/index ───────────────────────────────
@router.post("/index")
def index(body: IndexRequest, background_tasks: BackgroundTasks):
    storage_path = body.storage_path.strip()
    if not storage_path:
        raise HTTPException(status_code=400, detail="storage_path is required.")

    with Session(engine) as db:
        set_archive_path(db, storage_path)
        db.commit()

    if not Path(storage_path).exists():
        return {
            "message": "Path saved. Connect storage to begin indexing.",
            "storage_path": storage_path,
        }

    task_id = f"IDX-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(_index_walk, storage_path, task_id)
    return {"task_id": task_id, "status": "queued", "storage_path": storage_path}


def _index_walk(storage_path: str, task_id: str) -> None:
    """Background: walk the path and fingerprint any media files found.

    Best-effort and defensive — a failure on one file must not abort the walk.
    Each indexed file gets an archive_clips row (parent) + archive_fingerprints
    row. Duplicate hashes (UNIQUE constraint) are skipped.
    """
    root = Path(storage_path)
    if not root.exists():
        return

    indexed = 0
    with Session(engine) as db:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext not in _IMAGE_EXTS and ext not in _VIDEO_EXTS:
                continue
            media_type = "video" if ext in _VIDEO_EXTS else "image"
            try:
                data = path.read_bytes()
                fhash = _FP.hash_from_bytes(data, media_type)
            except Exception:  # noqa: BLE001
                continue

            exists = db.execute(
                text("SELECT 1 FROM archive_fingerprints WHERE fingerprint_hash = :h"),
                {"h": fhash},
            ).first()
            if exists:
                continue

            try:
                clip_id = db.execute(
                    text(
                        "INSERT INTO archive_clips "
                        "(title, keywords, file_path, recorded_at, fingerprint_hash, "
                        " duration_seconds) "
                        "VALUES (:title, '{}', :path, NOW(), :h, 1) "
                        "RETURNING id"
                    ),
                    {"title": path.stem, "path": str(path), "h": fhash},
                ).scalar_one()
                db.execute(
                    text(
                        "INSERT INTO archive_fingerprints "
                        "(clip_id, fingerprint_hash, fingerprint_type, algorithm) "
                        "VALUES (:clip_id, :h, :ftype, 'phash')"
                    ),
                    {
                        "clip_id": clip_id,
                        "h": fhash,
                        "ftype": media_type.upper() if media_type == "video" else "ARCHIVAL",
                    },
                )
                db.commit()
                indexed += 1
            except Exception:  # noqa: BLE001
                db.rollback()
                continue
