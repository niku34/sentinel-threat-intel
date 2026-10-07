"""Accuracy dashboard — per-module precision metrics combining real DB stats
with honest estimated accuracy ranges.

All estimates are labeled as such; real measurements come from the threats,
channel_snapshots, cert_in_drafts and fact_checks tables.
"""

from fastapi import APIRouter, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import engine
from models.cert_in_draft import CertInDraft
from models.snapshot import ChannelSnapshot
from routers.kaaval import Threat
from routers.veriff import FactCheck

router = APIRouter(prefix="/api/accuracy", tags=["accuracy"])

MEASURED = "measured"    # counted from the database, right now
ESTIMATED = "estimated"  # design target / early observation, NOT ground-truth measured


def _m(value, note: str | None = None) -> dict:
    """A metric read from the database."""
    return {"value": value, "data_source": MEASURED, "note": note}


def _e(value, note: str | None = None) -> dict:
    """An estimate. Never presented as a measurement."""
    return {
        "value": value,
        "data_source": ESTIMATED,
        "note": note or "Estimated — not measured against a labeled ground-truth dataset.",
    }


@router.get("/trend")
@limiter.limit("60/minute")
def accuracy_trend(request: Request) -> dict:
    with engine.connect() as conn:
        # Kaaval: confidence stored as decimal in judge->'brand_misuse'->>'confidence'
        try:
            kaaval_rows = conn.execute(text("""
                SELECT
                    DATE(detected_at) AS day,
                    AVG(CAST(judge->'brand_misuse'->>'confidence' AS FLOAT)) AS avg_confidence,
                    COUNT(*) AS count
                FROM threats
                WHERE detected_at >= NOW() - INTERVAL '30 days'
                  AND judge->'brand_misuse'->>'confidence' IS NOT NULL
                GROUP BY DATE(detected_at)
                ORDER BY day ASC
            """)).fetchall()
        except Exception:
            kaaval_rows = []

        # Veriff: direct confidence column (0–1 float)
        try:
            veriff_rows = conn.execute(text("""
                SELECT
                    DATE(checked_at) AS day,
                    COUNT(*) AS checks,
                    AVG(confidence) AS avg_confidence
                FROM fact_checks
                WHERE checked_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(checked_at)
                ORDER BY day ASC
            """)).fetchall()
        except Exception:
            veriff_rows = []

    kaaval_trend = [
        {"day": str(r[0]), "avg_confidence": round(float(r[1]) * 100, 1), "count": int(r[2])}
        for r in kaaval_rows if r[1] is not None
    ]

    veriff_trend = [
        {"day": str(r[0]), "checks": int(r[1]), "avg_confidence": round(float(r[2]) * 100, 1)}
        for r in veriff_rows if r[2] is not None
    ]

    # Overall: mean of whichever modules have data for each day
    day_map: dict[str, list[float]] = {}
    for pt in kaaval_trend:
        day_map.setdefault(pt["day"], []).append(pt["avg_confidence"])
    for pt in veriff_trend:
        day_map.setdefault(pt["day"], []).append(pt["avg_confidence"])

    overall_trend = sorted(
        [{"day": d, "confidence": round(sum(vs) / len(vs), 1)} for d, vs in day_map.items()],
        key=lambda x: x["day"],
    )

    return {
        "kaaval_trend": kaaval_trend,
        "veriff_trend": veriff_trend,
        "overall_trend": overall_trend,
    }


@router.get("/dashboard")
@limiter.limit("60/minute")
def accuracy_dashboard(request: Request) -> dict:
    with Session(engine) as session:
        # ── Kaaval AI ────────────────────────────────────────────────────────
        total_threats = session.execute(
            select(func.count()).select_from(Threat)
        ).scalar_one() or 0

        confirmed_threats = session.execute(
            select(func.count()).select_from(Threat).where(Threat.status == "confirmed")
        ).scalar_one() or 0

        dismissed_threats = session.execute(
            select(func.count()).select_from(Threat).where(Threat.status == "dismissed")
        ).scalar_one() or 0

        threats_by_type_rows = session.execute(
            select(Threat.threat_type, func.count()).group_by(Threat.threat_type)
        ).all()
        threats_by_type = {row[0]: row[1] for row in threats_by_type_rows}

        kaaval_fp_rate = (
            round(dismissed_threats / total_threats * 100, 1)
            if total_threats > 0 and dismissed_threats > 0
            else None
        )

        # ── Digital Kavasam ──────────────────────────────────────────────────
        total_anomalies = session.execute(
            select(func.count()).select_from(ChannelSnapshot)
            .where(ChannelSnapshot.anomaly_type.isnot(None))
        ).scalar_one() or 0

        cert_in_count = session.execute(
            select(func.count()).select_from(CertInDraft)
        ).scalar_one() or 0

        # ── Veriff ─────────────────────────────────────────────────────
        total_checks = session.execute(
            select(func.count()).select_from(FactCheck)
        ).scalar_one() or 0

        verdict_rows = session.execute(
            select(FactCheck.verdict, func.count()).group_by(FactCheck.verdict)
        ).all()
        verdict_breakdown = {row[0]: row[1] for row in verdict_rows}

        avg_verdict_time = session.execute(
            select(func.avg(
                func.extract("epoch", FactCheck.checked_at) -
                func.extract("epoch", FactCheck.checked_at)
            ))
        ).scalar_one()

        # Try avg verdict_time_seconds column directly
        try:
            avg_seconds = session.execute(
                select(func.avg(FactCheck.verdict_time_seconds))
            ).scalar_one()
        except Exception:
            avg_seconds = None

    # Real channel count. Counts only channels that are actually monitorable —
    # rows still holding a setup placeholder for channel_id are not scanned and
    # must not be counted (a placeholder row previously inflated this to 5).
    try:
        with engine.connect() as conn:
            channels_monitored = conn.execute(text(
                "SELECT count(*) FROM channels "
                "WHERE status = 'active' "
                "  AND coalesce(trim(channel_id), '') NOT IN ('', 'PASTE_ID_HERE')"
            )).scalar() or 0
    except Exception:
        channels_monitored = None

    return {
        "legend": {
            MEASURED: "Counted from the platform database at request time.",
            ESTIMATED: (
                "Design target or early operational observation. NOT measured against a labeled "
                "ground-truth dataset. Requires 30 days of editorial confirm/dismiss feedback."
            ),
        },
        "kaaval": {
            "total_threats": _m(int(total_threats)),
            "confirmed_threats": _m(int(confirmed_threats)),
            "dismissed_threats": _m(int(dismissed_threats)),
            "threats_by_type": _m(threats_by_type),
            "false_positive_rate": _m(
                kaaval_fp_rate,
                "Observed dismissal rate — not the same as a validated false-positive rate.",
            ),
            "precision_low": _e(75),
            "precision_high": _e(85),
            "precision_label": "Estimated — requires 30 days editorial feedback",
            "features": {
                "fake_page_detection": _e(90),
                "brigade_detection": _e(85),
                "deepfake_detection": _e(72),
                "tamil_nlp_classification": _e(78),
            },
            "note": "Accuracy improves as editorial team confirms/dismisses findings",
        },
        "kavasam": {
            "channels_monitored": _m(int(channels_monitored)) if channels_monitored is not None
                                  else _m(None, "Channel count unavailable"),
            "total_anomalies": _m(int(total_anomalies)),
            "cert_in_drafts": _m(int(cert_in_count)),
            "detection_rate": _e(99),
            "false_positive_rate_label": _e("<1%"),
            "strongest_feature": "Video deletion detection",
            "features": {
                "video_deletion_detection": _e(99),
                "unknown_admin_detection": _e(96),
                "cert_in_report_accuracy": _e(95),
                "anomaly_baselining": _e(88),
            },
            "note": "API-based detection — expected to be the most reliable module, but the "
                    "detection rate below is an estimate, not a measurement.",
        },
        "veriff": {
            "total_checks": _m(int(total_checks)),
            "verdict_breakdown": _m(verdict_breakdown),
            "avg_verdict_time_seconds": _m(
                round(float(avg_seconds), 1) if avg_seconds else None
            ),
            "precision_low": _e(70),
            "precision_high": _e(78),
            "precision_label": "Estimated — weakest module, improving",
            "strongest_source": "AltNews + Vishvas (highest match rate)",
            "weakest_area": "Celebrity/entertainment claims (improved Day 8 — capped at 60%)",
            "features": {
                # Reverse *video* search is non-functional — the InVID API host
                # does not resolve. Only image-side pHash/PIL forensics run.
                "reverse_image_search": _e(
                    90,
                    "Estimated — image-side pHash/PIL forensics only. Reverse VIDEO "
                    "search is not available (InVID API host does not resolve).",
                ),
                "fact_check_db_matching": _e(85),
                "tamil_claim_extraction": _e(78),
                "deepfake_detection": _e(72),
                "celebrity_entertainment": _e(60),
            },
            "note": "Weakest module. Target 85% after the semantic upgrade.",
        },
        "overall": {
            "platform_reliability": _e(92),
            "most_accurate_module": "Digital Kavasam",
            "least_accurate_module": "Veriff",
            "accuracy_improves_with": "30 days of editorial confirm/dismiss feedback",
            "next_milestone": "85% Veriff precision after semantic upgrade",
        },
    }
