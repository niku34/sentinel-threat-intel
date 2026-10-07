"""
User management CRUD.

The production `users` table uses pgcrypto field encryption + RLS policies
that are not yet provisioned for sentinel_app. This router persists team
members to api/data/team_members.json.

Two constraints worth knowing before adding records here:

1. **A row in this file does not create a login.** Authentication is served
   from the hardcoded ``_USERS`` dict in ``routers/auth.py``. Only accounts
   present there can actually sign in. The file previously listed four
   additional members (Editorial Head, IT Admin, Example News News Desk,
   Compliance Officer) who existed nowhere else in the system and could not
   authenticate — they were removed as fabricated roster entries.

2. **``last_login`` is never written.** Nothing in the codebase updates it, so
   it stays null. Do not populate it by hand; a hand-written timestamp is a
   fabricated access record. Real login history lives in ``audit_log`` and is
   exposed by ``GET /api/security/login-history``.

Emails here are stored in **plaintext**. Migrating auth to the encrypted
``users`` table is tracked in infrastructure/DPDP-compliance.md.
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.auth import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])

_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "team_members.json"
_lock = threading.Lock()

VALID_ROLES = {"SUPER_ADMIN", "EDITOR", "IT_ADMIN", "JOURNALIST", "VIEWER"}

# Hardcoded current user — matches auth.py _USERS seed for admin@example.com
_CURRENT_USER_EMAIL = "admin@example.com"


# ── File I/O ───────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    if not _DATA_FILE.exists():
        return []
    return json.loads(_DATA_FILE.read_text())


def _save(members: list[dict]) -> None:
    _DATA_FILE.write_text(json.dumps(members, indent=2, default=str))


def _safe_member(m: dict) -> dict:
    return {k: v for k, v in m.items() if k not in ("password_hash",)}


# ── Schemas ────────────────────────────────────────────────────────────────

class CreateUser(BaseModel):
    name: str
    email: str
    role: str
    password: str


class UpdateRole(BaseModel):
    role: str


# ── GET /api/users ─────────────────────────────────────────────────────────

@router.get("")
def list_users():
    with _lock:
        members = _load()
    members.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return {"users": [_safe_member(m) for m in members], "total": len(members)}


# ── POST /api/users ────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_user(body: CreateUser):
    if body.role not in VALID_ROLES:
        raise HTTPException(400, detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}")
    if not body.password or len(body.password) < 6:
        raise HTTPException(400, detail="Password must be at least 6 characters")

    with _lock:
        members = _load()
        if any(m["email"].lower() == body.email.lower() for m in members):
            raise HTTPException(400, detail="Email already exists")

        new_member = {
            "id": str(uuid4()),
            "name": body.name.strip(),
            "email": body.email.lower().strip(),
            "role": body.role,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_login": None,
            "password_hash": hash_password(body.password),
        }
        members.append(new_member)
        _save(members)

    return _safe_member(new_member)


# ── PATCH /api/users/{id}/role ─────────────────────────────────────────────

@router.patch("/{user_id}/role")
def update_role(user_id: str, body: UpdateRole):
    if body.role not in VALID_ROLES:
        raise HTTPException(400, detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}")

    with _lock:
        members = _load()
        target = next((m for m in members if m["id"] == user_id), None)
        if not target:
            raise HTTPException(404, detail="User not found")
        if target["email"] == _CURRENT_USER_EMAIL:
            raise HTTPException(403, detail="Cannot change your own role")
        target["role"] = body.role
        _save(members)

    return {"id": user_id, "role": body.role}


# ── PATCH /api/users/{id}/deactivate ──────────────────────────────────────

@router.patch("/{user_id}/deactivate")
def deactivate_user(user_id: str):
    with _lock:
        members = _load()
        target = next((m for m in members if m["id"] == user_id), None)
        if not target:
            raise HTTPException(404, detail="User not found")
        if target["email"] == _CURRENT_USER_EMAIL:
            raise HTTPException(403, detail="Cannot deactivate your own account")
        target["is_active"] = False
        _save(members)
    return {"id": user_id, "is_active": False}


# ── PATCH /api/users/{id}/activate ────────────────────────────────────────

@router.patch("/{user_id}/activate")
def activate_user(user_id: str):
    with _lock:
        members = _load()
        target = next((m for m in members if m["id"] == user_id), None)
        if not target:
            raise HTTPException(404, detail="User not found")
        target["is_active"] = True
        _save(members)
    return {"id": user_id, "is_active": True}
