import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from core.ollama_client import ollama_client
from models.channel import engine

router = APIRouter(prefix="/api/health", tags=["health"])

_AGENTS = Path(__file__).resolve().parents[2] / "agents"
_NOW_TS = lambda: datetime.now(timezone.utc).isoformat()


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


# ── Individual checks ──────────────────────────────────────────────────────

def _check_db_ping() -> dict:
    try:
        t = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "response_time_ms": _ms(t)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _check_ollama() -> dict:
    try:
        t = time.perf_counter()
        models = ollama_client.tags()
        ms = _ms(t)
        model_names = [m["name"] for m in models]
        has_qwen = any("qwen2.5:14b" in m for m in model_names)
        return {
            "ok": True,
            "model_available": has_qwen,
            "response_time_ms": ms,
        }
    except Exception as exc:
        return {"ok": False, "model_available": False, "error": str(exc)[:120]}


def _db_count(query: str) -> int | None:
    try:
        with engine.connect() as conn:
            return conn.execute(text(query)).scalar_one()
    except Exception:
        return None


def _file_exists(rel: str) -> bool:
    return (_AGENTS / rel).exists()


# ── Module health builders ─────────────────────────────────────────────────

def _kaaval_health() -> dict:
    venv_ok = (_AGENTS / ".venv" / "bin" / "python").exists()
    classifier_ok = _file_exists("veriff/claim_classifier.py")
    agents_ok = venv_ok and classifier_ok

    ollama = _check_ollama()
    db_ping = _check_db_ping()
    threats_24h = _db_count(
        "SELECT COUNT(*) FROM threats WHERE detected_at >= NOW() - INTERVAL '24 hours'"
    )

    if not db_ping["ok"]:
        status = "DOWN"
    elif not ollama["ok"] or not ollama.get("model_available"):
        status = "DEGRADED"
    elif ollama.get("response_time_ms", 0) > 5000:
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    return {
        "status": status,
        "threats_24h": threats_24h,
        "checks": {
            "ollama": "OK" if ollama["ok"] else "FAIL",
            "ollama_ms": ollama.get("response_time_ms"),
            "database": "OK" if db_ping["ok"] else "FAIL",
            "agents": "OK" if agents_ok else "MISSING",
        },
        "last_check": _NOW_TS(),
    }


def _kavasam_health() -> dict:
    db_ping = _check_db_ping()
    scans_24h = _db_count(
        "SELECT COUNT(*) FROM scans WHERE status='completed' AND started_at >= NOW() - INTERVAL '24 hours'"
    )
    snapshots_24h = _db_count(
        "SELECT COUNT(*) FROM channel_snapshots WHERE scanned_at >= NOW() - INTERVAL '24 hours'"
    )

    if not db_ping["ok"]:
        status = "DOWN"
    elif not scans_24h and not snapshots_24h:
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    return {
        "status": status,
        "scans_24h": scans_24h,
        "snapshots_24h": snapshots_24h,
        "checks": {
            "database": "OK" if db_ping["ok"] else "FAIL",
            "scans": "OK" if scans_24h else "NO_RECENT",
        },
        "last_check": _NOW_TS(),
    }


def _veriff_health() -> dict:
    scrapers_ok = _file_exists("veriff/scrapers.py")
    verdict_ok = _file_exists("veriff/verdict_engine.py")
    agents_ok = scrapers_ok and verdict_ok

    db_ping = _check_db_ping()
    checks_24h = _db_count(
        "SELECT COUNT(*) FROM fact_checks WHERE checked_at >= NOW() - INTERVAL '24 hours'"
    )

    if not db_ping["ok"]:
        status = "DOWN"
    elif not agents_ok:
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    return {
        "status": status,
        "checks_24h": checks_24h,
        "checks": {
            "database": "OK" if db_ping["ok"] else "FAIL",
            "agents": "OK" if agents_ok else "MISSING",
            "scrapers": "OK" if scrapers_ok else "MISSING",
            "verdict_engine": "OK" if verdict_ok else "MISSING",
        },
        "last_check": _NOW_TS(),
    }


def _ollama_health() -> dict:
    result = _check_ollama()
    if not result["ok"]:
        status = "DOWN"
    elif not result.get("model_available"):
        status = "DEGRADED"
    else:
        status = "HEALTHY"
    return {
        "status": status,
        "model": "qwen2.5:14b",
        "model_available": result.get("model_available", False),
        "response_time_ms": result.get("response_time_ms"),
        "last_check": _NOW_TS(),
    }


def _database_health() -> dict:
    result = _check_db_ping()
    return {
        "status": "HEALTHY" if result["ok"] else "DOWN",
        "response_time_ms": result.get("response_time_ms"),
        "last_check": _NOW_TS(),
    }


# ── GET /api/health/modules ────────────────────────────────────────────────

@router.get("/modules")
def module_health():
    kaaval = _kaaval_health()
    kavasam = _kavasam_health()
    veriff = _veriff_health()
    ollama = _ollama_health()
    database = _database_health()
    api = {"status": "HEALTHY", "last_check": _NOW_TS()}

    statuses = [kaaval["status"], kavasam["status"], veriff["status"],
                ollama["status"], database["status"]]
    if "DOWN" in statuses:
        overall = "DOWN"
    elif "DEGRADED" in statuses:
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"

    return {
        "modules": {
            "kaaval": kaaval,
            "kavasam": kavasam,
            "veriff": veriff,
            "ollama": ollama,
            "database": database,
            "api": api,
        },
        "overall": overall,
        "checked_at": _NOW_TS(),
    }
