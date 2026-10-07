"""Meta (Facebook + Instagram) integration API.

Configure credentials, test connectivity, read the composite Meta threat score,
and trigger authenticated Graph API scans.

HUMAN-IN-THE-LOOP: scans only produce status='detected' threats for editorial
review. Nothing here auto-strikes, auto-reports, or auto-files on Meta — the
confirm → draft → file-manually workflow lives downstream (Content Protection).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import engine

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_PYTHON = _PROJECT_ROOT / "agents" / ".venv" / "bin" / "python"

router = APIRouter(prefix="/api/meta", tags=["meta"])

_SLUGS = ("brand_main", "brand_spiritual", "brand_entertainment")


# ── config helpers ────────────────────────────────────────
def _load() -> dict:
    with Session(engine) as db:
        rows = db.execute(
            text("SELECT key, value FROM platform_config WHERE module = 'meta'")
        ).fetchall()
    return {k: v for k, v in rows}


def _save(pairs: dict) -> None:
    with Session(engine) as db:
        for key, value in pairs.items():
            db.execute(
                text(
                    "INSERT INTO platform_config (module, key, value, updated_at) "
                    "VALUES ('meta', :k, :v, NOW()) "
                    "ON CONFLICT (module, key) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = NOW()"
                ),
                {"k": key, "v": str(value)},
            )
        db.commit()


def _is_configured(cfg: dict) -> bool:
    has_app = bool(cfg.get("meta_app_id")) and bool(cfg.get("meta_app_secret"))
    has_page_token = any(cfg.get(f"meta_page_token_{s}") for s in _SLUGS)
    return has_app and has_page_token


def _missing_credentials(cfg: dict) -> list[str]:
    missing: list[str] = []
    if not cfg.get("meta_app_id"):
        missing.append("app_id")
    if not cfg.get("meta_app_secret"):
        missing.append("app_secret")
    if not any(cfg.get(f"meta_page_token_{s}") for s in _SLUGS):
        missing.append("at_least_one_page_token")
    return missing


def _score_level(score: int) -> str:
    if score >= 61:
        return "CRITICAL" if score >= 81 else "HIGH"
    if score >= 31:
        return "MEDIUM"
    return "LOW"


# ── models ────────────────────────────────────────────────
class BrandMap(BaseModel):
    brand_main: str = ""
    brand_spiritual: str = ""
    brand_entertainment: str = ""


class CredentialsRequest(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    page_tokens: BrandMap = BrandMap()
    page_ids: BrandMap = BrandMap()
    ig_accounts: BrandMap = BrandMap()


# ── GET /api/meta/status ──────────────────────────────────
@router.get("/status")
def status():
    """Configuration state of the Meta integration.

    'configured' means credential fields are filled in — it does NOT mean the
    credentials work. Call GET /api/meta/test for that. This endpoint used to
    report configured/enabled while the credentials were rejected by Meta.
    """
    cfg = _load()

    # Count DISTINCT tokens. All four page-token slots previously held the same
    # pasted string, which counted as "4 page tokens set" and read as four
    # configured pages.
    token_values = {cfg.get(f"meta_page_token_{s}") for s in _SLUGS}
    token_values.discard(None)
    token_values.discard("")
    page_tokens_set = sum(1 for s in _SLUGS if cfg.get(f"meta_page_token_{s}"))
    distinct_page_tokens = len(token_values)

    ig_accounts_set = sum(1 for s in _SLUGS if cfg.get(f"meta_instagram_account_{s}"))
    try:
        score = int(cfg.get("meta_threat_score", "0") or 0)
    except ValueError:
        score = 0

    warnings: list[str] = []
    if page_tokens_set > 1 and distinct_page_tokens == 1:
        warnings.append(
            f"All {page_tokens_set} page-token slots hold the same token — each "
            f"Broadcaster property needs its own page access token."
        )
    if not ig_accounts_set:
        warnings.append("No Instagram accounts configured — Instagram checks will not run.")

    return {
        "credentials_present": _is_configured(cfg),
        # Retained for existing callers; means "credential fields filled in",
        # not "connection verified".
        "configured": _is_configured(cfg),
        "connection_verified": None,
        "verify_hint": "Call GET /api/meta/test to check the credentials against Meta.",
        "enabled": str(cfg.get("meta_integration_enabled", "false")).lower() == "true",
        "missing_credentials": _missing_credentials(cfg),
        "page_tokens_set": page_tokens_set,
        "distinct_page_tokens": distinct_page_tokens,
        "ig_accounts_set": ig_accounts_set,
        "warnings": warnings,
        "meta_threat_score": score,
        "last_scan": cfg.get("meta_last_scan") or None,
    }


# ── POST /api/meta/credentials ────────────────────────────
@router.post("/credentials")
def save_credentials(body: CredentialsRequest):
    pairs = {
        "meta_app_id": body.app_id,
        "meta_app_secret": body.app_secret,
        "meta_integration_enabled": "true",
    }
    for slug in _SLUGS:
        pairs[f"meta_page_token_{slug}"] = getattr(body.page_tokens, slug, "")
        pairs[f"meta_page_id_{slug}"] = getattr(body.page_ids, slug, "")
        pairs[f"meta_instagram_account_{slug}"] = getattr(body.ig_accounts, slug, "")
    _save(pairs)
    return {
        "saved": True,
        "configured": _is_configured(_load()),
        "message": "Meta integration activated",
    }


# ── GET /api/meta/test ────────────────────────────────────
@router.get("/test")
def test_connection():
    """Verify each saved page token against the Meta Graph API.

    App-level tokens (app_id|app_secret) can't be used against page-scoped
    endpoints — this now calls each page's own node with its own page access
    token, which is what page tokens are actually good for.
    """
    cfg = _load()
    pages: list[dict] = []

    for slug in _SLUGS:
        token = cfg.get(f"meta_page_token_{slug}", "")
        page_id = cfg.get(f"meta_page_id_{slug}", "")
        if not token or not page_id:
            continue
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v18.0/{page_id}",
                params={"fields": "name,fan_count", "access_token": token},
                timeout=10,
            )
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            pages.append(
                {"name": None, "page_id": page_id, "fan_count": None, "status": "FAILED", "error": str(exc)}
            )
            continue

        if resp.status_code == 200 and "error" not in data:
            pages.append(
                {
                    "name": data.get("name"),
                    "page_id": page_id,
                    "fan_count": data.get("fan_count"),
                    "status": "OK",
                    "error": None,
                }
            )
        else:
            err = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
            pages.append(
                {
                    "name": None,
                    "page_id": page_id,
                    "fan_count": None,
                    "status": "FAILED",
                    "error": err or f"HTTP {resp.status_code}",
                }
            )

    connected = any(p["status"] == "OK" for p in pages)
    return {"connected": connected, "pages": pages}


# ── POST /api/meta/scan ───────────────────────────────────
@router.post("/scan")
@limiter.limit("6/minute")
def run_scan(request: Request):
    """Trigger the authenticated Meta scan in the agents venv (has requests+DB)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PROJECT_ROOT)
    code = (
        "import json; from agents.kaaval.fake_page_detector import scan_meta; "
        "print(json.dumps(scan_meta()))"
    )
    try:
        proc = subprocess.run(
            [str(_AGENTS_PYTHON), "-c", code],
            cwd=str(_PROJECT_ROOT), env=env,
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Meta scan timed out.")
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Meta scan failed: {proc.stderr.strip()[:400]}")
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise HTTPException(status_code=500, detail="Meta scan returned invalid output.")

    # Record last scan time (regardless of configured state).
    _save({"meta_last_scan": datetime.now(timezone.utc).isoformat()})
    return result


# ── GET /api/meta/threat-score ────────────────────────────
@router.get("/threat-score")
def threat_score():
    cfg = _load()
    if not _is_configured(cfg):
        return {"score": 0, "level": "LOW", "configured": False}
    try:
        score = int(cfg.get("meta_threat_score", "0") or 0)
    except ValueError:
        score = 0
    return {"score": score, "level": _score_level(score), "configured": True}
