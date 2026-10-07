"""Admin Console configuration endpoints.

Currently exposes the archive storage path used by the fingerprinting engine.
Stored in platform_config (module='archive', key='archive_storage_path') — the
same row the archive router reads, so the two stay in sync.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.channel import engine
from routers.archive import (
    DEFAULT_STORAGE_PATH,
    get_archive_path,
    set_archive_path,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ArchivePathRequest(BaseModel):
    path: str


@router.get("/config/archive-path")
def get_archive_path_config():
    with Session(engine) as db:
        path = get_archive_path(db) or DEFAULT_STORAGE_PATH
    return {"path": path}


@router.post("/config/archive-path")
def save_archive_path_config(body: ArchivePathRequest):
    path = body.path.strip()
    with Session(engine) as db:
        set_archive_path(db, path)
        db.commit()
    return {"saved": True, "path": path}
