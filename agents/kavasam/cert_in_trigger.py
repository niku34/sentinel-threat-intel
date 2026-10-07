"""CERT-In auto-draft trigger for Digital Kavasam.

When a CRITICAL channel anomaly is detected (e.g. mass video deletion / possible
takedown), draft a CERT-In incident report so a human can review and file it
within the 6-hour window.

HUMAN-IN-THE-LOOP: this only creates a DRAFT (status='DRAFT'). Sentinel never
auto-files with CERT-In — an operator reviews the draft and submits it manually.
Duplicate drafts are suppressed: if a DRAFT already exists for this channel in
the last 24h, no new draft is created.
"""
from __future__ import annotations

import logging
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("cert_in_trigger")

_ORG = "Example Broadcaster"
_REPORTING_EMAIL = "incident@cert-in.org.in"
_DEADLINE_HOURS = 6


def _reference_number(now: datetime) -> str:
    return f"CERTIN-KAVASAM-{now.strftime('%Y%m%d')}-{random.randint(0, 9999):04d}"


def maybe_create_certin_draft(session, channel_name: str, anomaly_detail: str) -> str | None:
    """Create a CERT-In DRAFT for a channel anomaly, unless a recent one exists.

    Returns the draft id (str) if one was created, else None.
    """
    from sqlalchemy import text

    affected_system = f"YouTube channel: {channel_name}"
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    existing = session.execute(
        text(
            "SELECT id FROM cert_in_drafts "
            "WHERE affected_system = :sys AND status = 'DRAFT' AND created_at >= :since "
            "LIMIT 1"
        ),
        {"sys": affected_system, "since": since},
    ).first()
    if existing:
        logger.info("CERT-In draft already exists for %s in last 24h — skipping", channel_name)
        return None

    now = datetime.now(timezone.utc)
    reference = _reference_number(now)
    deadline = now + timedelta(hours=_DEADLINE_HOURS)
    description = (
        f"Digital Kavasam detected a CRITICAL anomaly on {affected_system}. "
        f"{anomaly_detail}. This may indicate unauthorized access, a takedown, or "
        f"content tampering affecting a Example Broadcaster broadcast property."
    )
    impact = (
        "Potential loss of published broadcast content and audience reach; "
        "possible unauthorized administrative access to a public channel."
    )
    report_json = {
        "reference_number": reference,
        "report_generated_at": now.isoformat(),
        "reporting_to": _REPORTING_EMAIL,
        "regulation": "CERT-In Directions, 28 April 2022 — 6-hour reporting rule",
        "deadline": {
            "deadline_at": deadline.isoformat(),
            "deadline_hours": _DEADLINE_HOURS,
            "status": "WITHIN_WINDOW",
        },
        "organisation": {"name": _ORG, "contact_email": "contact@example.com"},
        "incident": {
            "type": "CHANNEL_ANOMALY",
            "affected_system": affected_system,
            "detected_at": now.isoformat(),
            "description": description,
            "impact": impact,
        },
        "status": "DRAFT",
        "note": "DRAFT — prepared by Digital Kavasam. Review and file manually.",
    }

    row = session.execute(
        text(
            "INSERT INTO cert_in_drafts "
            "(reference_number, incident_type, affected_system, detected_at, "
            " description, impact, reported_by, report_json, status) "
            "VALUES (:ref, 'CHANNEL_ANOMALY', :sys, :det, :desc, :imp, "
            " 'Digital Kavasam (auto-draft)', CAST(:rep AS jsonb), 'DRAFT') "
            "RETURNING id"
        ),
        {
            "ref": reference,
            "sys": affected_system,
            "det": now,
            "desc": description,
            "imp": impact,
            "rep": __import__("json").dumps(report_json),
        },
    ).first()
    logger.warning("CERT-In DRAFT %s created for %s", reference, channel_name)
    return str(row[0]) if row else None
