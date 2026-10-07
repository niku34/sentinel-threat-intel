import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from models.channel import Base, Channel, engine
from websocket_manager import emit_scan_event, emit_threat

_logger = logging.getLogger("sentinel")

_AGENTS_PYTHON = (
    Path(__file__).resolve().parents[2] / "agents" / ".venv" / "bin" / "python"
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

router = APIRouter(prefix="/api/scan", tags=["scan"])

VALID_TARGETS = {"brand_news", "brand_main", "brand_entertainment", "brand_movies", "all"}
VALID_SCAN_TYPES = {"full", "quick"}

# Map UI scan_type values to the DB check constraint's allowed values
_SCAN_TYPE_DB: dict[str, str] = {"full": "full", "quick": "incremental"}

# target slug → substring of channel name (case-insensitive match)
_TARGET_NAME_HINT: dict[str, str] = {
    "brand_main": "broadcaster tv",
    "brand_news": "broadcaster plus",
    "brand_entertainment": "broadcaster max",
    "brand_movies": "j movie",
}

# In-memory map of scan_id -> target label; persisted only for the process lifetime
# (the scans table channel_id FK covers persistence; the label is UI sugar)
_scan_targets: dict[str, str] = {}


class ScanJob(Base):
    __tablename__ = "scans"
    __table_args__ = {"extend_existing": True}

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    channel_id = Column(PG_UUID(as_uuid=True), ForeignKey("channels.id"), nullable=False)
    scan_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="running")
    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    completed_at = Column(DateTime(timezone=True))
    findings_count = Column(Integer, default=0)
    error_message = Column(Text)


# Only creates table if it doesn't exist; safe against existing schema
Base.metadata.create_all(engine, tables=[ScanJob.__table__])


def _resolve_channel_id(session: Session, target: str) -> UUID:
    """Return the channel.id for a target slug, or the first channel for 'all'."""
    hint = _TARGET_NAME_HINT.get(target)
    if hint:
        channels = session.execute(select(Channel)).scalars().all()
        for ch in channels:
            if hint in ch.name.lower():
                return ch.id
    # fallback: first channel in table (covers 'all' and unmatched)
    first = session.execute(select(Channel).limit(1)).scalar_one_or_none()
    if not first:
        raise HTTPException(status_code=503, detail="No channels in database")
    return first.id


class ScanRequest(BaseModel):
    target: str
    scan_type: str


def _run_kaaval() -> int:
    script = (
        "import sys; sys.path.insert(0, '.'); "
        "from agents.kaaval.threat_intel_agent import scan_threats; "
        "import json; print(json.dumps(scan_threats()))"
    )
    result = subprocess.run(
        [str(_AGENTS_PYTHON), "-c", script],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        # scan_threats() now enforces its own ~25s soft budget per phase
        # (4 phases), so this is a safety margin on top of that internal
        # bound, not the primary defense against a slow scan.
        timeout=150,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout).get("total_threats", 0)


def _run_kavasam(scan_id: str) -> int:
    # scan_id is passed through so a CRITICAL anomaly (e.g. mass video
    # deletion) can be persisted as a real findings-table row tied to this
    # scan job (findings.scan_id is a NOT NULL FK to scans.id).
    script = (
        "import sys, json; sys.path.insert(0, '.'); "
        "from agents.kavasam.channel_health_agent import scan_channels; "
        "print(json.dumps(scan_channels(scan_id=sys.argv[1])))"
    )
    result = subprocess.run(
        [str(_AGENTS_PYTHON), "-c", script, scan_id],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout).get("anomalies_found", 0)


def _broadcast_new_threats(since: datetime) -> None:
    """Stream every threat created during this scan window to the live feed."""
    try:
        from routers.kaaval import Threat

        with Session(engine) as session:
            new_threats = (
                session.execute(
                    select(Threat)
                    .where(Threat.detected_at >= since)
                    .order_by(Threat.detected_at.asc())
                )
                .scalars()
                .all()
            )
        for t in new_threats:
            emit_threat({
                "id": str(t.id),
                "threat_type": t.threat_type,
                "severity": t.severity,
                "title": t.title,
                "detected_at": t.detected_at.isoformat() if t.detected_at else None,
                "source": "KAAVAL",
            })
    except Exception:
        pass


def _execute_scan(scan_id: str, scan_type: str) -> None:
    started = datetime.now(timezone.utc)
    findings = 0
    error_message: str | None = None
    try:
        findings += _run_kaaval()
        if scan_type == "full":
            findings += _run_kavasam(scan_id)
    except subprocess.TimeoutExpired as exc:
        error_message = f"{exc.cmd[-1] if exc.cmd else 'agent subprocess'} timed out after {exc.timeout}s"
        _logger.exception("Scan %s: agent subprocess timed out", scan_id)
    except Exception as exc:
        error_message = str(exc) or exc.__class__.__name__
        _logger.exception("Scan %s: agent subprocess failed", scan_id)

    # Stream the newly-created threats to any live-feed listeners.
    _broadcast_new_threats(started)

    with Session(engine) as session:
        job = session.get(ScanJob, UUID(scan_id))
        if job:
            job.status = "failed" if error_message else "completed"
            job.error_message = error_message
            job.completed_at = datetime.now(timezone.utc)
            job.findings_count = findings
            session.commit()

    emit_scan_event("scan_completed", {
        "scan_id": scan_id,
        "findings_count": findings,
        "status": "failed" if error_message else "completed",
        "error_message": error_message,
    })


@router.post("/start")
def start_scan(payload: ScanRequest, background_tasks: BackgroundTasks):
    if payload.target not in VALID_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=f"target must be one of {sorted(VALID_TARGETS)}",
        )
    if payload.scan_type not in VALID_SCAN_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"scan_type must be one of {sorted(VALID_SCAN_TYPES)}",
        )

    scan_id = uuid4()
    _scan_targets[str(scan_id)] = payload.target

    with Session(engine) as session:
        channel_id = _resolve_channel_id(session, payload.target)
        session.add(
            ScanJob(
                id=scan_id,
                channel_id=channel_id,
                scan_type=_SCAN_TYPE_DB[payload.scan_type],
                status="running",
                started_at=datetime.now(timezone.utc),
                findings_count=0,
            )
        )
        session.commit()

    emit_scan_event("scan_started", {
        "scan_id": str(scan_id),
        "target": payload.target,
        "scan_type": payload.scan_type,
    })

    background_tasks.add_task(_execute_scan, str(scan_id), payload.scan_type)

    short_id = str(scan_id)[:8].upper()
    return {
        "scan_id": str(scan_id),
        "status": "started",
        "message": f"Scan #{short_id} initiated",
    }


@router.get("/{scan_id}/status")
def get_scan_status(scan_id: str):
    try:
        uid = UUID(scan_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scan_id")

    with Session(engine) as session:
        job = session.get(ScanJob, uid)
        if not job:
            raise HTTPException(status_code=404, detail="Scan not found")
        return {
            "scan_id": str(job.id),
            "target": _scan_targets.get(str(job.id)),
            "scan_type": job.scan_type,
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "findings_count": job.findings_count,
            "error_message": job.error_message,
        }
