import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.models.channel import Base, Channel, engine  # noqa: E402
from api.services.youtube_service import get_channel_stats  # noqa: E402
from agents.judge.judge_llm import validate_kavasam_anomaly  # noqa: E402
from agents.kavasam.cert_in_trigger import maybe_create_certin_draft  # noqa: E402
from agents.kavasam.findings_writer import insert_finding  # noqa: E402
from agents.kavasam.youtube_client import (  # noqa: E402
    VIDEO_DROP_ABS_FLOOR,
    VIDEO_DROP_PCT,
    YouTubeClient,
    _most_severe,
    display_channel_name,
    is_configured_channel,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("channel_health_agent")

# Subscriber/video-drop thresholds now live solely in youtube_client
# (KAVASAM_VIDEO_DROP_* / the hardcoded 10% subscriber-drop rule in
# detect_anomalies) — this module delegates to it instead of keeping a
# second, divergent copy. See _detect_anomaly()'s docstring.
EXPECTED_PUBLIC_STATUS = os.getenv("EXPECTED_CHANNEL_STATUS", "public")

# Per-channel soft time budget for one scan step (API/scrape fetch + detection).
# Same pattern as threat_intel_agent._run_phase_with_budget — a single stuck
# network call must not stall the whole "+ New Scan" sweep.
_CHANNEL_SCAN_BUDGET_SECONDS = float(os.getenv("KAVASAM_CHANNEL_SCAN_BUDGET_SECONDS", "20"))
# Judge-LLM validation of a detected anomaly — deliberately tighter than the
# module's own 60s JUDGE_LLM_TIMEOUT; see _validate_with_budget().
_JUDGE_VALIDATION_BUDGET_SECONDS = float(
    os.getenv("KAVASAM_JUDGE_VALIDATION_BUDGET_SECONDS", "25")
)

# Reused for detect_anomalies() — pure logic, no network/credentials needed,
# so a single shared instance is safe and avoids re-instantiating per channel.
_YT_DETECTOR = YouTubeClient()


class ChannelSnapshot(Base):
    __tablename__ = "channel_snapshots"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    channel_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("channels.id"), nullable=False
    )
    subscriber_count = Column(Integer)
    video_count = Column(Integer)
    view_count = Column(Integer)
    status = Column(Text)
    anomaly_detected = Column(Boolean, nullable=False, default=False)
    anomaly_type = Column(Text)
    anomaly_detail = Column(Text)
    scanned_at = Column(DateTime(timezone=True), server_default=func.now())
    judge = Column(JSONB, nullable=True)


Base.metadata.create_all(engine, tables=[ChannelSnapshot.__table__])


def _latest_snapshot(session: Session, channel_pk: UUID) -> ChannelSnapshot | None:
    stmt = (
        select(ChannelSnapshot)
        .where(ChannelSnapshot.channel_id == channel_pk)
        .order_by(ChannelSnapshot.scanned_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _detect_anomaly(
    stats: dict,
    previous: ChannelSnapshot | None,
    previous_age_seconds: float | None = None,
) -> tuple[bool, str | None, str | None, str]:
    """Returns (anomaly_detected, anomaly_type, anomaly_detail, severity).

    Mass video deletion is the CRITICAL case here: a channel losing videos is
    the strongest signal of unauthorized access, a platform takedown, or a
    hijacked account wiping content. Subscriber-count and video-count
    thresholds delegate to YouTubeClient.detect_anomalies() — the exact same
    logic the dedicated Kavasam page's own scan already used — so the
    "+ New Scan" sweep and that page can never disagree about what counts as
    a mass deletion.

    Threshold rationale (see youtube_client.py VIDEO_DROP_* constants): a
    fixed absolute count is wrong at both ends of the library-size spectrum —
    it under-reacts on a 200,000-video channel and over-reacts on a 400-video
    one. So the rule is relative-to-library-size with an absolute floor:
    mass deletion fires at >= max(25 videos, 2% of the previous library),
    whichever is larger. That floor was calibrated against real observed
    deltas on these channels (e.g. Example News +373 on a 212,632-video library
    is routine upload churn and must not fire; a multi-thousand-video drop on
    the same channel must). A smaller "partial delete" band (>= max(10, 0.5%))
    exists one severity tier down for unlisting/targeted-takedown signal that
    isn't yet a mass event.
    """
    if "error" in stats:
        return True, "SCAN_ERROR", f"No data returned from API: {stats['error']}", "HIGH"

    current_status = stats.get("status")
    if current_status and current_status != EXPECTED_PUBLIC_STATUS:
        # Catches a bad status on the very first-ever scan of a channel (no
        # prior snapshot to diff against yet) — detect_anomalies() below only
        # ever compares against a previous snapshot, so it can't catch this.
        return (
            True,
            "CHANNEL_STATUS_CHANGE",
            f"Channel status is '{current_status}' (expected '{EXPECTED_PUBLIC_STATUS}')",
            "HIGH",
        )

    if previous is None:
        return False, None, None, "LOW"

    prev_dict = {
        "subscriber_count": previous.subscriber_count,
        "video_count": previous.video_count,
        "status": previous.status,
    }
    anomalies = _YT_DETECTOR.detect_anomalies(stats, prev_dict, previous_age_seconds)
    top = _most_severe(anomalies)
    if not top:
        return False, None, None, "LOW"
    return True, top["anomaly_type"], top["detail"], top["severity"]


def _fetch_stats_with_budget(channel_id: str) -> dict:
    """Fetch one channel's stats under a soft time budget.

    get_channel_stats() already carries its own 10s HTTP timeout, but this is
    a belt-and-suspenders ceiling on the whole step (fetch + whatever the
    caller does next) — same tradeoff as
    threat_intel_agent._run_phase_with_budget: the worker thread isn't force-
    killed (Python can't do that), it's abandoned, so the function returns
    promptly instead of stalling the rest of the "+ New Scan" sweep on one
    slow channel.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(get_channel_stats, channel_id).result(
            timeout=_CHANNEL_SCAN_BUDGET_SECONDS
        )
    except FutureTimeoutError:
        logger.warning(
            "Channel stats fetch for %s exceeded its %.0fs budget — skipping "
            "this channel for this scan cycle",
            channel_id, _CHANNEL_SCAN_BUDGET_SECONDS,
        )
        return {"error": f"stats fetch exceeded {_CHANNEL_SCAN_BUDGET_SECONDS:.0f}s budget"}
    finally:
        pool.shutdown(wait=False)


def _validate_with_budget(base_record: dict) -> dict | None:
    """Judge-LLM validation under a soft time budget.

    validate_kavasam_anomaly() has its own internal httpx timeout (60s,
    JUDGE_LLM_TIMEOUT), but a real test run measured ~120s wall time for a
    single call — a cold/slow Ollama can burn through that budget and then
    some (retry behaviour internal to the judge module). That alone can
    exceed the API's 120s scan subprocess ceiling, and because scan_channels()
    only committed once at the very end of the whole run, a subprocess kill
    at that ceiling would have discarded EVERY channel's work for the run —
    including an already-detected CRITICAL mass-deletion anomaly and its
    CERT-In draft. This bounds the judge call specifically; a timeout here
    just means "not validated", exactly like judge_llm's own internal
    fallback — it never blocks the anomaly/CERT-In/finding path above it.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(validate_kavasam_anomaly, base_record).result(
            timeout=_JUDGE_VALIDATION_BUDGET_SECONDS
        )
    except FutureTimeoutError:
        logger.warning(
            "Judge LLM validation exceeded its %.0fs budget for %s — "
            "proceeding unvalidated rather than risking the whole scan",
            _JUDGE_VALIDATION_BUDGET_SECONDS, base_record.get("name"),
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Judge LLM validation failed: %s", exc)
        return None
    finally:
        pool.shutdown(wait=False)


def scan_channels(scan_id: str | None = None) -> dict:
    """Run one Digital Kavasam sweep: fetch stats, diff against the last
    snapshot, and act on the most severe anomaly found per channel.

    `scan_id` ties any findings-table row to the "+ New Scan" job that
    triggered it (findings.scan_id is a NOT NULL FK to scans.id). It's
    optional — a standalone/CLI run without a scan_id still detects and
    persists to channel_snapshots and can still trigger a CERT-In draft;
    only the findings-table write is skipped without one.
    """
    logger.info("Starting channel health scan (scan_id=%s)", scan_id)
    results: list[dict] = []
    anomalies_found = 0
    critical_findings: list[str] = []

    with Session(engine) as session:
        channels = session.execute(select(Channel)).scalars().all()
        logger.info("Loaded %d channels from database", len(channels))

        for channel in channels:
            if not is_configured_channel(channel.channel_id):
                logger.warning(
                    "Skipping channel '%s' — no real channel_id configured (%r). "
                    "Scanning an unconfigured row produces fictitious metrics.",
                    channel.name,
                    channel.channel_id,
                )
                continue
            logger.info(
                "Scanning channel name=%s channel_id=%s",
                channel.name,
                channel.channel_id,
            )
            stats = _fetch_stats_with_budget(channel.channel_id)
            previous = _latest_snapshot(session, channel.id)
            previous_age_seconds = None
            if previous and previous.scanned_at:
                scanned = previous.scanned_at
                if scanned.tzinfo is None:
                    scanned = scanned.replace(tzinfo=timezone.utc)
                previous_age_seconds = (datetime.now(timezone.utc) - scanned).total_seconds()

            anomaly, anomaly_type, anomaly_detail, severity = _detect_anomaly(
                stats, previous, previous_age_seconds
            )

            base_record = {
                "channel_id": channel.channel_id,
                "name": channel.name,
                "subscriber_count": stats.get("subscriber_count"),
                "video_count": stats.get("video_count"),
                "view_count": stats.get("view_count"),
                "status": stats.get("status"),
                "anomaly_detected": anomaly,
                "anomaly_type": anomaly_type,
                "anomaly_detail": anomaly_detail,
                "severity": severity if anomaly else None,
                "fetched_at": stats.get("fetched_at"),
            }

            judge_payload: dict | None = None
            if anomaly:
                validated = _validate_with_budget(base_record)
                if validated:
                    judge_payload = {
                        k: v for k, v in validated.items() if k.startswith("judge_")
                    }
                    base_record.update(judge_payload)

            # CRITICAL anomaly (mass video deletion is the primary case today)
            # → CERT-In draft + a real findings-table row. Both are best-effort:
            # a failure here must never lose the channel_snapshots record
            # (which is already staged on `session` below and is what
            # /api/kavasam and /api/findings actually read today).
            cert_in_draft_id: str | None = None
            finding_id: str | None = None
            if anomaly and severity == "CRITICAL":
                display_name = display_channel_name(channel.name, channel.channel_id)
                try:
                    cert_in_draft_id = maybe_create_certin_draft(
                        session, display_name, anomaly_detail
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "CERT-In draft trigger failed for %s: %s", display_name, exc
                    )
                finding_id = insert_finding(
                    engine,
                    scan_id=scan_id,
                    severity="critical",
                    module="kavasam",
                    title=f"{anomaly_type}: {display_name}",
                    description=anomaly_detail or "Critical channel anomaly detected",
                )
                if finding_id:
                    critical_findings.append(finding_id)

            judge_blob = judge_payload
            if cert_in_draft_id or finding_id:
                judge_blob = {
                    **(judge_blob or {}),
                    **({"cert_in_draft_id": cert_in_draft_id} if cert_in_draft_id else {}),
                    **({"finding_id": finding_id} if finding_id else {}),
                }

            snapshot = ChannelSnapshot(
                channel_id=channel.id,
                subscriber_count=stats.get("subscriber_count"),
                video_count=stats.get("video_count"),
                view_count=stats.get("view_count"),
                status=stats.get("status"),
                anomaly_detected=anomaly,
                anomaly_type=anomaly_type,
                anomaly_detail=anomaly_detail,
                scanned_at=datetime.now(timezone.utc),
                judge=judge_blob,
            )
            session.add(snapshot)

            if anomaly:
                anomalies_found += 1
                logger.warning(
                    "Anomaly on %s [%s/%s]: %s",
                    channel.name,
                    severity,
                    anomaly_type,
                    anomaly_detail,
                )

            results.append(base_record)

            # Commit per-channel, not once at the end of the loop. The judge-
            # LLM validation above can run long (a real test measured ~120s
            # on a cold Ollama — close to the API's own subprocess ceiling);
            # if the subprocess gets killed by that outer timeout mid-loop, a
            # single end-of-loop commit would have discarded every channel's
            # work for this run, including an already-detected CRITICAL
            # anomaly and its CERT-In draft. Committing here bounds any such
            # loss to the channel currently in flight, not the whole scan.
            session.commit()

    summary = {
        "total_channels": len(results),
        "anomalies_found": anomalies_found,
        "critical_findings": critical_findings,
        "results": results,
    }
    logger.info(
        "Scan complete: %d channels, %d anomalies, %d critical finding(s)",
        summary["total_channels"],
        summary["anomalies_found"],
        len(critical_findings),
    )
    return summary


if __name__ == "__main__":
    summary = scan_channels()
    logger.info("Summary: %s", summary)
