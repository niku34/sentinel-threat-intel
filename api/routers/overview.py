from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.channel import engine

router = APIRouter(prefix="/api/overview", tags=["overview"])


@router.get("")
def get_overview():
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_str = since_24h.isoformat()

    with Session(engine) as db:
        # ── scans_24h ─────────────────────────────────────────
        try:
            threats_total = db.execute(
                text("SELECT COUNT(*) FROM threats WHERE detected_at >= :since"),
                {"since": since_str},
            ).scalar() or 0
        except Exception:
            threats_total = 0

        try:
            checks_total = db.execute(
                text("SELECT COUNT(*) FROM fact_checks WHERE checked_at >= :since"),
                {"since": since_str},
            ).scalar() or 0
        except Exception:
            checks_total = 0

        try:
            snaps_total = db.execute(
                text("SELECT COUNT(*) FROM channel_snapshots WHERE scanned_at >= :since"),
                {"since": since_str},
            ).scalar() or 0
        except Exception:
            snaps_total = 0

        scans_24h = int(threats_total) + int(checks_total) + int(snaps_total)

        # ── critical_findings ─────────────────────────────────
        try:
            critical_findings = db.execute(
                text(
                    "SELECT COUNT(*) FROM threats "
                    "WHERE severity = 'CRITICAL' AND detected_at >= :since"
                ),
                {"since": since_str},
            ).scalar() or 0
            critical_findings = int(critical_findings)
        except Exception:
            critical_findings = 0

        # ── judge_validations_24h ────────────────────────────────
        try:
            judge_threats = db.execute(
                text(
                    "SELECT COUNT(*) FROM threats "
                    "WHERE judge IS NOT NULL AND detected_at >= :since"
                ),
                {"since": since_str},
            ).scalar() or 0
        except Exception:
            judge_threats = 0

        try:
            judge_checks = db.execute(
                text(
                    "SELECT COUNT(*) FROM fact_checks "
                    "WHERE judge IS NOT NULL AND checked_at >= :since"
                ),
                {"since": since_str},
            ).scalar() or 0
        except Exception:
            judge_checks = 0

        judge_validations_24h = int(judge_threats) + int(judge_checks)

        # ── endpoints_watched ─────────────────────────────────
        try:
            endpoints_watched = db.execute(
                text("SELECT COUNT(DISTINCT channel_id) FROM channel_snapshots")
            ).scalar() or 0
            endpoints_watched = int(endpoints_watched)
        except Exception:
            endpoints_watched = None

        # ── severity_breakdown ────────────────────────────────
        sev_breakdown = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        sev_map = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
        for sev_key, sev_label in sev_map.items():
            try:
                count = db.execute(
                    text(
                        "SELECT COUNT(*) FROM threats "
                        "WHERE severity = :sev AND detected_at >= :since"
                    ),
                    {"sev": sev_key, "since": since_str},
                ).scalar() or 0
                sev_breakdown[sev_label] = int(count)
            except Exception:
                pass

        # ── module_stats.kaaval ───────────────────────────────
        try:
            kaaval_threats_24h = db.execute(
                text("SELECT COUNT(*) FROM threats WHERE detected_at >= :since"),
                {"since": since_str},
            ).scalar() or 0
            kaaval_threats_24h = int(kaaval_threats_24h)
        except Exception:
            kaaval_threats_24h = 0

        try:
            sources_monitored = db.execute(
                text("SELECT COUNT(DISTINCT source_platform) FROM threats")
            ).scalar() or 0
            sources_monitored = int(sources_monitored)
        except Exception:
            sources_monitored = None

        # ── module_stats.kavasam ──────────────────────────────
        try:
            channels_watched = db.execute(
                text("SELECT COUNT(DISTINCT channel_id) FROM channel_snapshots")
            ).scalar() or 0
            channels_watched = int(channels_watched)
        except Exception:
            channels_watched = None

        try:
            anomalies_24h = db.execute(
                text(
                    "SELECT COUNT(*) FROM channel_snapshots "
                    "WHERE anomaly_detected = true AND scanned_at >= :since"
                ),
                {"since": since_str},
            ).scalar() or 0
            anomalies_24h = int(anomalies_24h)
        except Exception:
            anomalies_24h = 0

        try:
            cert_in_drafts = db.execute(
                text("SELECT COUNT(*) FROM cert_in_drafts WHERE status = 'DRAFT'")
            ).scalar() or 0
            cert_in_drafts = int(cert_in_drafts)
        except Exception:
            cert_in_drafts = 0

        # ── module_stats.veriff ───────────────────────────────
        try:
            veriff_checks_today = db.execute(
                text("SELECT COUNT(*) FROM fact_checks WHERE checked_at >= :since"),
                {"since": since_str},
            ).scalar() or 0
            veriff_checks_today = int(veriff_checks_today)
        except Exception:
            veriff_checks_today = 0

        try:
            avg_vt = db.execute(
                text(
                    "SELECT AVG(verdict_time_seconds) FROM fact_checks "
                    "WHERE checked_at >= :since"
                ),
                {"since": since_str},
            ).scalar()
            avg_verdict_time_sec = round(float(avg_vt), 1) if avg_vt is not None else None
        except Exception:
            avg_verdict_time_sec = None

    return {
        "scans_24h": scans_24h,
        "critical_findings": critical_findings,
        "judge_validations_24h": judge_validations_24h,
        "endpoints_watched": endpoints_watched,
        "severity_breakdown": sev_breakdown,
        "module_stats": {
            "kaaval": {
                "threats_24h": kaaval_threats_24h,
                "sources_monitored": sources_monitored,
            },
            "kavasam": {
                "channels_watched": channels_watched,
                "anomalies_24h": anomalies_24h,
                "cert_in_drafts": cert_in_drafts,
            },
            "veriff": {
                "checks_today": veriff_checks_today,
                "avg_verdict_time_sec": avg_verdict_time_sec,
            },
        },
        "generated_at": now.isoformat(),
    }
