import asyncio
import json
import logging
import random
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from core.config import settings
from core.limiter import limiter
from websocket_manager import manager
from routers.accuracy import router as accuracy_router
from routers.admin import router as admin_router
from routers.archive import router as archive_router
from routers.audit import router as audit_router
from routers.auth import router as auth_router
from routers.brand_misuse import router as brand_misuse_router
from routers.certin import router as certin_router
from routers.config import router as config_router
from routers.deepfake import router as deepfake_router
from routers.feedback import router as feedback_router
from routers.findings import router as findings_router
from routers.kaaval import router as kaaval_router
from routers.kavasam import router as kavasam_router
from routers.llm_wiki import router as llm_wiki_router
from routers.meta import router as meta_router
from routers.plugins import router as plugins_router
from routers.rights_manager import router as rights_manager_router
from routers.module_health import router as module_health_router
from routers.notifications import router as notifications_router
from routers.overview import router as overview_router
from routers.scan import router as scan_router
from routers.security import router as security_router
from routers.users import router as users_router
from routers.veriff import router as veriff_router
from routers.voice import router as voice_router

_KILL_SWITCH_FILE = Path(__file__).resolve().parents[1] / "KILL_SWITCH_STATE.json"


def _read_kill_switch() -> dict:
    if not _KILL_SWITCH_FILE.exists():
        return {"active": False, "triggered_at": None}
    try:
        return json.loads(_KILL_SWITCH_FILE.read_text())
    except Exception:
        return {"active": False, "triggered_at": None}


def _write_kill_switch(active: bool) -> dict:
    state = {
        "active": active,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "triggered_by": "admin",
    }
    _KILL_SWITCH_FILE.write_text(json.dumps(state, indent=2))
    return state


class KillSwitchRequest(BaseModel):
    action: str  # "pause" | "resume"


def _check_db() -> dict:
    url = settings.database_url
    if not url:
        return {"connected": False, "detail": "DATABASE_URL not set"}
    try:
        conn = psycopg2.connect(url, connect_timeout=3)
        conn.close()
        return {"connected": True, "detail": "ok"}
    except Exception as exc:
        return {"connected": False, "detail": str(exc)}


_logger = logging.getLogger("sentinel")
_EMBED_SERVER_HEALTH = "http://127.0.0.1:11435/health"


async def _ensure_embedding_server() -> None:
    """Start the embedding server if it isn't already running."""
    import httpx

    repo_root = Path(__file__).resolve().parents[1]
    python = repo_root / "agents" / ".venv" / "bin" / "python"
    script = repo_root / "agents" / "veriff" / "embedding_server.py"

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(_EMBED_SERVER_HEALTH)
            if resp.status_code == 200:
                _logger.info("Embedding server already running on port 11435.")
                return
    except Exception:
        pass  # server not up yet — start it

    if not python.exists() or not script.exists():
        _logger.warning(
            "Embedding server not startable: %s or %s missing.", python, script
        )
        return

    log_path = Path("/tmp/embedding-server.log")
    with log_path.open("a") as fh:
        subprocess.Popen(
            [str(python), str(script)],
            stdout=fh,
            stderr=subprocess.STDOUT,
        )
    _logger.info(
        "Embedding server started on port 11435 (log: %s)", log_path
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Capture the running loop so sync handlers / background tasks can schedule
    # WebSocket broadcasts via run_coroutine_threadsafe.
    manager.bind_loop(asyncio.get_running_loop())
    await _ensure_embedding_server()
    yield


app = FastAPI(
    title="Broadcaster Shield API",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    lambda req, exc: JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": "Too many requests — please wait before retrying",
        },
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(accuracy_router)
app.include_router(admin_router)
app.include_router(archive_router)
app.include_router(audit_router)
app.include_router(auth_router)
app.include_router(brand_misuse_router)
app.include_router(certin_router)
app.include_router(config_router)
app.include_router(deepfake_router)
app.include_router(feedback_router)
app.include_router(findings_router)
app.include_router(kavasam_router)
app.include_router(kaaval_router)
app.include_router(llm_wiki_router)
app.include_router(meta_router)
app.include_router(plugins_router)
app.include_router(rights_manager_router)
app.include_router(module_health_router)
app.include_router(notifications_router)
app.include_router(overview_router)
app.include_router(scan_router)
app.include_router(security_router)
app.include_router(users_router)
app.include_router(veriff_router)
app.include_router(voice_router)


# ── /api/verify alias ────────────────────────────────────────
# Public rebuild contract wants /api/verify. Delegate to the veriff
# router so we keep one implementation.
from routers.veriff import (  # noqa: E402
    FactCheckRequest as _VerifyRequest,
    verify_claim as _verify_claim,
)


@app.post("/api/verify")
def verify_alias(request: Request, payload: _VerifyRequest):
    return _verify_claim(request, payload)


# ── WebSocket · Live Operations feed ─────────────────────────
@app.websocket("/ws/live-feed")
async def live_feed(websocket: WebSocket):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from models.channel import engine
    from routers.kaaval import Threat

    await manager.connect(websocket)
    try:
        # Backfill: last 20 threats, oldest-first so the client can prepend each
        # and end up with newest on top.
        with Session(engine) as session:
            recent = (
                session.execute(
                    select(Threat).order_by(Threat.detected_at.desc()).limit(20)
                )
                .scalars()
                .all()
            )
        for threat in reversed(recent):
            await websocket.send_json({
                "type": "threat",
                "id": str(threat.id),
                "threat_type": threat.threat_type,
                "severity": threat.severity,
                "title": threat.title,
                "detected_at": threat.detected_at.isoformat() if threat.detected_at else None,
                "source": "KAAVAL",
            })

        # Keep the socket alive with a periodic ping.
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:  # noqa: BLE001 — any send failure closes the socket
        manager.disconnect(websocket)


_TRANSLIT_WORDS: dict[str, str] = {
    "ஜெயா": "Broadcaster",
    "சென்னை": "Chennai",
    "போராட்டம்": "Pōrāttam",
    "காட்சிகள்": "Kaatchigal",
    "இன்று": "Indru",
    "காலை": "Kaalai",
    "நடைபெற்ற": "Nadaiperra",
}

_TRANSLIT_CHARS: dict[str, str] = {
    # vowels
    "அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "ee", "உ": "u",
    "ஊ": "oo", "எ": "e", "ஏ": "ae", "ஐ": "ai", "ஒ": "o",
    "ஓ": "oh", "ஔ": "au",
    # consonants
    "க": "ka", "ங": "nga", "ச": "cha", "ஞ": "nya", "ட": "ta",
    "ண": "na", "த": "tha", "ந": "na", "ப": "pa", "ம": "ma",
    "ய": "ya", "ர": "ra", "ல": "la", "வ": "va", "ழ": "zha",
    "ள": "la", "ற": "ra", "ன": "na",
    # grantha
    "ஜ": "ja", "ஷ": "sha", "ஸ": "sa", "ஹ": "ha",
    # vowel signs (matras)
    "ா": "aa", "ி": "i", "ீ": "ee", "ு": "u", "ூ": "oo",
    "ெ": "e", "ே": "ae", "ை": "ai", "ொ": "o", "ோ": "oh",
    "ௌ": "au", "்": "",
}


@app.get("/api/utils/transliterate")
async def transliterate(text: str = Query(...)):
    result = text
    for word, replacement in _TRANSLIT_WORDS.items():
        result = result.replace(word, replacement)
    output = "".join(_TRANSLIT_CHARS.get(ch, ch) for ch in result)
    return {"original": text, "transliterated": output}


@app.get("/health")
async def health():
    db = _check_db()
    return {
        "status": "ok" if db["connected"] else "degraded",
        "database": db,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/admin/kill-switch")
async def get_kill_switch():
    state = _read_kill_switch()
    return {
        "active": state.get("active", False),
        "triggered_at": state.get("triggered_at"),
    }


@app.post("/api/admin/kill-switch")
async def post_kill_switch(body: KillSwitchRequest):
    if body.action == "pause":
        _write_kill_switch(active=True)
        return {"status": "paused", "message": "All agents paused."}
    elif body.action == "resume":
        _write_kill_switch(active=False)
        return {"status": "active", "message": "All agents resumed."}
    else:
        raise HTTPException(status_code=400, detail="action must be 'pause' or 'resume'")


@app.get("/api/scan/status")
async def scan_status():
    from sqlalchemy import func, select, text
    from sqlalchemy.orm import Session
    from models.channel import engine
    from models.cert_in_draft import CertInDraft
    from models.snapshot import ChannelSnapshot
    from routers.kaaval import Threat
    from routers.veriff import FactCheck

    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_1h = now - timedelta(hours=1)

    def _status(last_run: datetime | None) -> str:
        if last_run is None:
            return "NO_DATA"
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        if last_run >= since_1h:
            return "ACTIVE"
        if last_run >= since_24h:
            return "RECENT"
        return "IDLE"

    def _iso(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    with Session(engine) as session:
        # kaaval
        kaaval_last = session.execute(
            select(func.max(Threat.detected_at))
        ).scalar_one()
        kaaval_count = session.execute(
            select(func.count()).select_from(Threat).where(Threat.detected_at >= since_24h)
        ).scalar_one()

        # kavasam
        kavasam_last = session.execute(
            select(func.max(ChannelSnapshot.scanned_at))
        ).scalar_one()
        kavasam_count = session.execute(
            select(func.count()).select_from(ChannelSnapshot)
            .where(ChannelSnapshot.anomaly_detected.is_(True))
            .where(ChannelSnapshot.scanned_at >= since_24h)
        ).scalar_one()

        # veriff
        veriff_last = session.execute(
            select(func.max(FactCheck.checked_at))
        ).scalar_one()
        veriff_count = session.execute(
            select(func.count()).select_from(FactCheck).where(FactCheck.checked_at >= since_24h)
        ).scalar_one()

        # judge — rows in threats with judge IS NOT NULL
        judge_last = session.execute(
            select(func.max(Threat.detected_at)).where(Threat.judge.isnot(None))
        ).scalar_one()
        judge_count = session.execute(
            select(func.count()).select_from(Threat)
            .where(Threat.judge.isnot(None))
            .where(Threat.detected_at >= since_24h)
        ).scalar_one()

        # certin
        certin_last = session.execute(
            select(func.max(CertInDraft.created_at))
        ).scalar_one()
        certin_drafts = session.execute(
            select(func.count()).select_from(CertInDraft).where(CertInDraft.status == "DRAFT")
        ).scalar_one()

        # audit
        audit_count = session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE created_at >= :since"),
            {"since": since_24h},
        ).scalar_one()

    scan_id = f"SCAN-{now.strftime('%Y%m%d')}-{random.randint(0, 9999):04d}"

    return {
        "scan_id": scan_id,
        "status": "idle",
        "agents": [
            {
                "name": "Kaaval AI",
                "module": "kaaval",
                "status": _status(kaaval_last),
                "last_run": _iso(kaaval_last),
                "threats_today": int(kaaval_count),
            },
            {
                "name": "Digital Kavasam",
                "module": "kavasam",
                "status": _status(kavasam_last),
                "last_run": _iso(kavasam_last),
                "anomalies_today": int(kavasam_count),
            },
            {
                "name": "Veriff",
                "module": "veriff",
                "status": _status(veriff_last),
                "last_run": _iso(veriff_last),
                "verdicts_today": int(veriff_count),
            },
            {
                "name": "Judge LLM",
                "module": "judge",
                "status": _status(judge_last),
                "last_run": _iso(judge_last),
                "validations_today": int(judge_count),
            },
            {
                "name": "CERT-In Drafter",
                "module": "certin",
                "status": _status(certin_last),
                "last_run": _iso(certin_last),
                "drafts_ready": int(certin_drafts),
            },
            {
                "name": "Audit Logger",
                "module": "audit",
                "status": "ACTIVE",
                "last_run": now.isoformat(),
                "events_today": int(audit_count),
            },
        ],
        "kill_switch_active": _read_kill_switch().get("active", False),
    }
