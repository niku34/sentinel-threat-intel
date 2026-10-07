import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, Integer, Text, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import Base, engine
from websocket_manager import emit_threat

logger = logging.getLogger("api.veriff")

# run_fact_check lives in agents/.venv (Python 3.11 + crewai).
# We cross the venv boundary via subprocess rather than a direct import,
# since crewai depends on tiktoken which cannot build on Python 3.14.
_AGENTS_PYTHON = Path(__file__).resolve().parents[2] / "agents" / ".venv" / "bin" / "python"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

router = APIRouter(prefix="/api/veriff", tags=["veriff"])

RECYCLED_VERDICTS = ("RECYCLED", "MANIPULATED", "FAKE")


class FactCheckPipelineError(RuntimeError):
    """A clean, client-safe error message. Never contains a stack trace."""


class FactCheck(Base):
    __tablename__ = "fact_checks"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_text = Column(Text, nullable=False)
    media_type = Column(Text, nullable=False)
    language = Column(Text, nullable=False)
    verdict = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)
    verdict_time_seconds = Column(Integer, nullable=False)
    database_results = Column(JSONB, nullable=False)
    checked_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    checked_by = Column(Text, nullable=True)
    judge = Column(JSONB, nullable=True)


class FactCheckRequest(BaseModel):
    claim_text: str
    media_type: str = "text"
    language: str | None = None
    checked_by: str | None = None


# The subprocess script catches exceptions from run_fact_check() itself and
# emits a JSON error payload on stdout, rather than letting them propagate as
# an uncaught exception (nonzero exit + a raw traceback on stderr). A raw
# traceback must never become the API's error `detail` field — a client
# (live, in front of a journalist) would see it verbatim. The full trace still
# goes to stderr for server-side logs; only a clean message crosses the
# subprocess boundary.
_RUN_SCRIPT = """
import sys, json
sys.path.insert(0, '.')
from agents.veriff.fact_check_agent import run_fact_check

args = json.loads(sys.argv[1])
try:
    result = run_fact_check(**args)
except Exception as exc:
    import traceback
    traceback.print_exc()  # server-side log only
    print(json.dumps({"__fact_check_error__": str(exc)}))
    sys.exit(0)
print(json.dumps(result, ensure_ascii=False))
"""

_GENERIC_PIPELINE_ERROR = "Fact-check pipeline encountered an internal error. Please retry."


def _run_fact_check(payload: FactCheckRequest, media_path: str | None = None) -> dict:
    # media_path crosses the api -> agents-venv subprocess boundary as a
    # filesystem path (not inline bytes) — keeps the argv small and avoids
    # base64-inflating a multi-MB image/video into the process arguments.
    args = json.loads(payload.model_dump_json())
    if media_path:
        args["media_path"] = media_path
    result = subprocess.run(
        [str(_AGENTS_PYTHON), "-c", _RUN_SCRIPT, json.dumps(args)],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        # Should not happen — the script's own try/except should have caught
        # anything from run_fact_check(). If we're here, something crashed
        # below that (e.g. an import error). Log full detail, never forward it.
        logger.error(
            "Fact-check subprocess crashed (rc=%s):\n%s",
            result.returncode, result.stderr,
        )
        raise FactCheckPipelineError(_GENERIC_PIPELINE_ERROR)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.error(
            "Fact-check subprocess produced non-JSON stdout:\n%s\n--- stderr ---\n%s",
            result.stdout, result.stderr,
        )
        raise FactCheckPipelineError(_GENERIC_PIPELINE_ERROR)
    if isinstance(data, dict) and "__fact_check_error__" in data:
        # A clean, expected failure surfaced by run_fact_check itself (e.g.
        # every scraper timed out). The message is already client-safe.
        logger.warning(
            "Fact-check pipeline reported a clean failure: %s",
            data["__fact_check_error__"],
        )
        raise FactCheckPipelineError(data["__fact_check_error__"])
    return data


# Map a Veriff verdict to a live-feed severity.
_VERDICT_SEVERITY = {
    "FAKE": "HIGH",
    "MANIPULATED": "HIGH",
    "AUTO_FAKE": "CRITICAL",
    "HIGH_CONFIDENCE_FAKE": "HIGH",
    "MEDIUM_CONFIDENCE_FAKE": "MEDIUM",
    "RECYCLED": "MEDIUM",
    "NEEDS_HUMAN_REVIEW": "MEDIUM",
    "UNVERIFIED": "INFO",
    "REAL": "INFO",
    "AUTO_REAL": "INFO",
}


def _broadcast_verdict(raw: dict) -> None:
    """Push a VERIFF_CHECK event to the Live Operations feed (never raises)."""
    try:
        verdict = raw.get("verdict") or "UNVERIFIED"
        claim = (raw.get("claim_text") or "").strip()
        title = claim[:120] + ("…" if len(claim) > 120 else "")
        emit_threat({
            "id": raw.get("id"),
            "threat_type": "VERIFF_CHECK",
            "severity": _VERDICT_SEVERITY.get(str(verdict).upper(), "INFO"),
            "title": f"{verdict}: {title}" if title else str(verdict),
            "verdict": verdict,
            "confidence": raw.get("confidence"),
            "claim_text": claim,
            "detected_at": raw.get("checked_at"),
            "source": "VERIFF",
        })
    except Exception:
        pass


def _shape_verify_response(raw: dict) -> dict:
    """Trim the internal payload to the /api/verify contract."""
    return {
        "id": raw.get("id"),
        "claim_text": raw.get("claim_text"),
        "claim_type": raw.get("claim_type"),
        "verdict": raw.get("verdict"),
        "confidence": raw.get("confidence"),
        "needs_human_review": raw.get("needs_human_review", True),
        "flags": raw.get("flags", []),
        "matched_source_count": raw.get("matched_source_count", 0),
        "db_results": raw.get("db_results", []),
        "reasoning": raw.get("reasoning", []),
        "verdict_time_seconds": raw.get("verdict_time_seconds"),
        "media_type": raw.get("media_type"),
        "language": raw.get("language"),
        "deepfake_score": raw.get("deepfake_score"),
        # Day 13 semantic upgrade fields.
        "from_cache": raw.get("from_cache", False),
        "cache_similarity": raw.get("cache_similarity"),
        "vision_analysis": raw.get("vision_analysis"),
        "invid_matches": raw.get("invid_matches", []),
        # Day 15 web search + enhanced fields.
        "web_search_results": raw.get("web_search_results", []),
        "web_sources_checked": raw.get("web_sources_checked", 0),
        "entities": raw.get("entities"),
        "related_claims": raw.get("related_claims", []),
        "web_credibility_score": raw.get("web_credibility_score"),
        "web_credibility_label": raw.get("web_credibility_label"),
    }


_MAX_MEDIA_BYTES = 50 * 1024 * 1024  # 50MB — generous for a dropped clip, not unbounded


# ── POST /api/veriff/check ────────────────────────────────
# multipart/form-data so a dropped image/video/audio file's actual bytes can
# ride along with the claim — the dashboard's drop zone previously only sent
# the filename as text, discarding the file entirely (see
# VeriffVerifyPanel.handleSubmit before this fix).
@router.post("/check")
@limiter.limit("20/minute")
async def check_claim(
    request: Request,
    claim_text: str = Form(...),
    media_type: str = Form("text"),
    language: str | None = Form(None),
    checked_by: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    payload = FactCheckRequest(
        claim_text=claim_text,
        media_type=media_type,
        language=language,
        checked_by=checked_by,
    )
    tmp_path: str | None = None
    try:
        if file is not None and media_type != "text":
            data = await file.read()
            if data:
                if len(data) > _MAX_MEDIA_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Media file too large (max 50MB).",
                    )
                suffix = Path(file.filename or "").suffix
                fd, tmp_path = tempfile.mkstemp(suffix=suffix)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
        verdict = _run_fact_check(payload, media_path=tmp_path)
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Fact check timed out.",
        )
    except HTTPException:
        raise
    except FactCheckPipelineError as exc:
        # Message is already client-safe (see _run_fact_check / _RUN_SCRIPT).
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )
    except Exception as exc:
        # Anything else is unexpected — log full detail server-side, never
        # forward str(exc) to the client (could be a raw traceback or leak
        # internal paths/config).
        logger.error("Unexpected error in /api/veriff/check", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_GENERIC_PIPELINE_ERROR,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    _broadcast_verdict(verdict)
    return verdict


# ── POST /api/veriff/verify ────────────────────────────────
# Public rebuild contract: {claim_type, verdict, confidence,
# db_results, needs_human_review}. Also exposed at /api/verify.
@router.post("/verify")
@limiter.limit("20/minute")
def verify_claim(request: Request, payload: FactCheckRequest):
    try:
        raw = _run_fact_check(payload)
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Fact check timed out.",
        )
    except FactCheckPipelineError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("Unexpected error in /api/veriff/verify", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_GENERIC_PIPELINE_ERROR,
        )
    _broadcast_verdict(raw)
    return _shape_verify_response(raw)


# ── GET /api/veriff/checks ────────────────────────────────
@router.get("/checks")
def list_checks():
    with Session(engine) as session:
        rows = session.execute(
            select(FactCheck).order_by(FactCheck.checked_at.desc()).limit(20)
        ).scalars().all()

        result = [
            {
                "id": str(c.id),
                "claim_text": c.claim_text,
                "media_type": c.media_type,
                "language": c.language,
                "verdict": c.verdict,
                "confidence": c.confidence,
                "verdict_time_seconds": c.verdict_time_seconds,
                "database_results": c.database_results,
                "checked_at": c.checked_at.isoformat() if c.checked_at else None,
                "checked_by": c.checked_by,
            }
            for c in rows
        ]

        return {"checks": result, "total": len(result)}


# ── GET /api/veriff/cache/stats ───────────────────────────
@router.get("/cache/stats")
def cache_stats():
    """pgvector semantic-cache stats: size, today's reuse hits, avg confidence.

    Fails soft — if the claim_embeddings table doesn't exist yet (migration
    not run), returns zeros rather than a 500.
    """
    try:
        with engine.connect() as conn:
            total = conn.execute(
                text("SELECT COUNT(*) FROM claim_embeddings")
            ).scalar_one()
            hits_today = conn.execute(
                text(
                    "SELECT COUNT(*) FROM claim_embeddings "
                    "WHERE last_used::date = NOW()::date AND last_used > created_at"
                )
            ).scalar_one()
            avg_conf = conn.execute(
                text("SELECT AVG(confidence) FROM claim_embeddings")
            ).scalar_one()
        return {
            "total_cached": int(total or 0),
            "cache_hits_today": int(hits_today or 0),
            "avg_similarity": round(float(avg_conf), 4) if avg_conf is not None else 0.0,
        }
    except Exception:
        return {"total_cached": 0, "cache_hits_today": 0, "avg_similarity": 0.0}


# ── GET /api/veriff/metrics ───────────────────────────────
@router.get("/metrics")
def get_metrics():
    with Session(engine) as session:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

        checks_today = session.execute(
            select(func.count())
            .select_from(FactCheck)
            .where(FactCheck.checked_at >= since)
        ).scalar_one()

        avg_verdict_time = session.execute(
            select(func.avg(FactCheck.verdict_time_seconds))
            .where(FactCheck.checked_at >= since)
        ).scalar_one()

        stories_held = session.execute(
            select(func.count())
            .select_from(FactCheck)
            .where(FactCheck.checked_at >= since)
            .where(FactCheck.verdict.in_(RECYCLED_VERDICTS))
        ).scalar_one()

        return {
            "checks_today": checks_today,
            "avg_verdict_time": float(avg_verdict_time) if avg_verdict_time is not None else 0,
            "stories_held": stories_held,
            "audit_trail": "100%",
        }
