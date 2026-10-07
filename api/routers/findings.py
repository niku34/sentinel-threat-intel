from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.channel import Channel, engine
from models.snapshot import ChannelSnapshot
from core.limiter import limiter
from routers.kaaval import Threat
from routers.veriff import FactCheck

router = APIRouter(prefix="/api/findings", tags=["findings"])

# ── Playbook reference data ───────────────────────────────
# Stored in-code; apply database/migrations/004_finding_playbooks.sql
# (as postgres) to persist in DB instead.
_PLAYBOOKS: dict[str, dict] = {
    "DEEPFAKE_AUDIO": {
        "threat_type": "DEEPFAKE_AUDIO",
        "what_it_means": "AI-cloned anchor voice circulating on social media",
        "remediation_steps": [
            "File Telegram/WhatsApp takedown via India safe-harbor",
            "Issue on-air clarification within 2 hours",
            "Add voice fingerprint to Kaaval probe library",
            "Log incident in audit trail",
        ],
        "who_to_contact": "Editorial Head + IT Admin",
        "legal_implications": "Defamation exposure + IT Act 2000 S.66D",
        "resolution_criteria": "Takedown confirmed + fingerprint added",
    },
    "BRIGADE_ATTACK": {
        "threat_type": "BRIGADE_ATTACK",
        "what_it_means": "Coordinated accounts mass-commenting to manipulate YouTube algorithm",
        "remediation_steps": [
            "Bulk-flag comments via YouTube Studio",
            "Report channel to YouTube Trust & Safety",
            "Add account pattern to Kaaval brigade detector",
            "Monitor for re-emergence 48h",
        ],
        "who_to_contact": "IT Admin",
        "legal_implications": "IT Act 2000 S.66 — harassment",
        "resolution_criteria": "Comments removed + accounts flagged",
    },
    "BRIGADE": {
        "threat_type": "BRIGADE",
        "what_it_means": "Coordinated accounts mass-commenting to manipulate YouTube algorithm",
        "remediation_steps": [
            "Bulk-flag comments via YouTube Studio",
            "Report channel to YouTube Trust & Safety",
            "Add account pattern to Kaaval brigade detector",
            "Monitor for re-emergence 48h",
        ],
        "who_to_contact": "IT Admin",
        "legal_implications": "IT Act 2000 S.66 — harassment",
        "resolution_criteria": "Comments removed + accounts flagged",
    },
    "FAKE_PAGE": {
        "threat_type": "FAKE_PAGE",
        "what_it_means": "Impersonator page using Example Broadcaster branding to mislead audience",
        "remediation_steps": [
            "File platform IP/impersonation report",
            "Document evidence (screenshots + URLs)",
            "Issue public warning on official channels",
            "Add page pattern to Kaaval monitor",
        ],
        "who_to_contact": "Editorial Head + Legal",
        "legal_implications": "IT Act S.66C + Trademark Act 1999",
        "resolution_criteria": "Page taken down or labelled fake",
    },
    "FOOTAGE_THEFT": {
        "threat_type": "FOOTAGE_THEFT",
        "what_it_means": "Example Broadcaster archival footage reused without permission",
        "remediation_steps": [
            "File DMCA takedown draft (auto-generated)",
            "Document original broadcast timestamp",
            "Contact platform rights team",
            "Log in DPDP audit trail",
        ],
        "who_to_contact": "Legal + IT Admin",
        "legal_implications": "Copyright Act 1957 + IT Act",
        "resolution_criteria": "Content removed or credited",
    },
    "ARCHIVE_MISUSE": {
        "threat_type": "ARCHIVE_MISUSE",
        "what_it_means": "Example Broadcaster archival footage reused without permission",
        "remediation_steps": [
            "File DMCA takedown draft (auto-generated)",
            "Document original broadcast timestamp",
            "Contact platform rights team",
            "Log in DPDP audit trail",
        ],
        "who_to_contact": "Legal + IT Admin",
        "legal_implications": "Copyright Act 1957 + IT Act",
        "resolution_criteria": "Content removed or credited",
    },
    "CERT_IN_INCIDENT": {
        "threat_type": "CERT_IN_INCIDENT",
        "what_it_means": "Security breach requiring mandatory 6-hour CERT-In report",
        "remediation_steps": [
            "File auto-drafted CERT-In report immediately",
            "Preserve all logs tamper-proof",
            "Notify internal stakeholders",
            "Conduct post-incident review",
        ],
        "who_to_contact": "IT Admin + CEO",
        "legal_implications": "CERT-In Directions 2022 — up to Rs.1 Crore penalty if missed",
        "resolution_criteria": "Report filed within 6 hours + logs preserved 180 days",
    },
    "RECYCLED_FOOTAGE": {
        "threat_type": "RECYCLED_FOOTAGE",
        "what_it_means": "Old Example Broadcaster or third-party footage recirculated with false context",
        "remediation_steps": [
            "Verify original broadcast date via archive fingerprint",
            "Issue correction on Example News newsroom",
            "Add clip hash to Veriff probe library",
            "Inform journalist who submitted for verification",
        ],
        "who_to_contact": "Editorial Head",
        "legal_implications": "Defamation risk if broadcast without verification",
        "resolution_criteria": "Correction issued + clip fingerprinted",
    },
    "LOGO_MISUSE": {
        "threat_type": "LOGO_MISUSE",
        "what_it_means": "Example Broadcaster logo used without authorization on external content",
        "remediation_steps": [
            "Document unauthorized usage with screenshots",
            "File platform IP complaint",
            "Send cease-and-desist via legal",
            "Add logo variant to detection model",
        ],
        "who_to_contact": "Legal",
        "legal_implications": "Trademark Act 1999 + Copyright Act 1957",
        "resolution_criteria": "Logo removed or licensing agreed",
    },
    "DEEPFAKE": {
        "threat_type": "DEEPFAKE",
        "what_it_means": "AI-cloned anchor voice or synthetic video circulating on social media",
        "remediation_steps": [
            "File Telegram/WhatsApp takedown via India safe-harbor",
            "Issue on-air clarification within 2 hours",
            "Add voice/face fingerprint to Kaaval probe library",
            "Log incident in audit trail",
        ],
        "who_to_contact": "Editorial Head + IT Admin",
        "legal_implications": "Defamation exposure + IT Act 2000 S.66D",
        "resolution_criteria": "Takedown confirmed + fingerprint added",
    },
    "IMPERSONATION": {
        "threat_type": "IMPERSONATION",
        "what_it_means": "Fake account or page impersonating Example Broadcaster brand or anchors",
        "remediation_steps": [
            "File platform IP/impersonation report",
            "Document evidence (screenshots + URLs)",
            "Issue public warning on official channels",
            "Add pattern to Kaaval monitor",
        ],
        "who_to_contact": "Editorial Head + Legal",
        "legal_implications": "IT Act S.66C + Trademark Act 1999",
        "resolution_criteria": "Account/page taken down or labelled fake",
    },
    "CHANNEL_STATUS_CHANGE": {
        "threat_type": "CHANNEL_STATUS_CHANGE",
        "what_it_means": "YouTube channel visibility or status changed without authorization — possible account compromise or platform action",
        "remediation_steps": [
            "Immediately verify change in YouTube Studio → Settings → Channel Status",
            "Check admin access log for unauthorized logins",
            "If unauthorized: rotate all YouTube account credentials within 30 minutes",
            "Enable 2-step verification if not already active",
            "If data breach suspected: file CERT-In report within 6 hours",
            "Document all changes with timestamps for DPDP audit trail",
        ],
        "who_to_contact": "IT Admin (immediate) + Editorial Head",
        "legal_implications": "IT Act 2000 S.43A — data security obligation. CERT-In Directions 2022 if credentials compromised",
        "resolution_criteria": "Channel status restored + credentials rotated + 2FA enabled + audit documented",
    },
    "SUBSCRIBER_DROP": {
        "threat_type": "SUBSCRIBER_DROP",
        "what_it_means": "Sudden subscriber loss detected — possible coordinated unsubscribe attack, YouTube bot purge, or content policy action",
        "remediation_steps": [
            "Check YouTube Studio Analytics for drop timeline",
            "Cross-reference with YouTube's known bot purge dates",
            "If organic purge: monitor for 48 hours, no action needed",
            "If coordinated attack: document accounts involved and report to YouTube Trust & Safety",
            "If content policy action: review flagged content in YouTube Studio",
            "Post transparent communication on official Example Broadcaster social if loss exceeds 5%",
        ],
        "who_to_contact": "IT Admin + Editorial Head",
        "legal_implications": "No immediate legal action — document for pattern analysis. If coordinated: IT Act 2000 S.66 harassment",
        "resolution_criteria": "Root cause identified + platform notified if coordinated + monitoring active for 7 days",
    },
    "VIDEO_COUNT_DROP": {
        "threat_type": "VIDEO_COUNT_DROP",
        "what_it_means": "One or more videos deleted from Example Broadcaster channel — could be unauthorized deletion, hacking, or accidental removal",
        "remediation_steps": [
            "URGENT: Check YouTube Studio → Content → Deleted videos immediately",
            "If unauthorized deletion: CHANGE ALL PASSWORDS IMMEDIATELY",
            "Enable 2-step verification if not active",
            "Check for unauthorized admin accounts in YouTube Studio → Settings → Permissions",
            "File CERT-In report within 6 hours (mandatory breach notification)",
            "Attempt to restore from YouTube's 21-day recovery window",
            "Contact YouTube Partner Support for emergency recovery",
            "Backup all remaining videos to NAS archive",
        ],
        "who_to_contact": "IT Admin (IMMEDIATE) + CEO",
        "legal_implications": "CERT-In Directions 2022 — mandatory report within 6 hours. IT Act 2000 S.43 if malicious deletion",
        "resolution_criteria": "Videos restored or documented as unrecoverable + credentials secured + CERT-In filed + backup verified",
    },
    "VIDEO_COUNT_SPIKE": {
        "threat_type": "VIDEO_COUNT_SPIKE",
        "what_it_means": "Unusual bulk video upload detected — could indicate account compromise with malicious content or unauthorized access",
        "remediation_steps": [
            "Open YouTube Studio → Content and review all recently uploaded videos",
            "If unauthorized uploads: remove immediately (do NOT leave up)",
            "Change all YouTube account passwords immediately",
            "Revoke all third-party app access in Google Account settings",
            "Check admin devices for malware",
            "File CERT-In if account was clearly compromised",
            "Review who has upload access in YouTube Studio → Permissions",
        ],
        "who_to_contact": "IT Admin",
        "legal_implications": "IT Act 2000 S.43 if malicious access. Platform ToS violation if spam content uploaded",
        "resolution_criteria": "Unauthorized content removed + credentials secured + access audit complete",
    },
    "ANCHOR_IMPERSONATION": {
        "threat_type": "ANCHOR_IMPERSONATION",
        "what_it_means": "Social media account impersonating a Example Broadcaster anchor or journalist — misleading audience with fake content attributed to real staff",
        "remediation_steps": [
            "Screenshot all evidence (profile, posts, URL) immediately — accounts get deleted quickly",
            "Report to platform using official impersonation report form: Instagram/Facebook/X/YouTube",
            "Inform the impersonated anchor so they can report personally (platform prioritizes victim reports)",
            "Issue public warning on official Example Broadcaster channels identifying the fake account",
            "Add anchor name and likeness to Kaaval monitoring probe library",
            "If defamatory content posted: preserve evidence for legal action",
            "File police complaint if financial fraud or serious defamation involved",
        ],
        "who_to_contact": "Editorial Head + Legal + impersonated anchor directly",
        "legal_implications": "IT Act 2000 S.66C (identity theft) + S.66D (cheating by impersonation). Cognizable offence — FIR can be filed",
        "resolution_criteria": "Platform report filed + public warning issued + account removed or labeled fake + anchor informed",
    },
    "INSTAGRAM_MISUSE": {
        "threat_type": "INSTAGRAM_MISUSE",
        "what_it_means": "Instagram account misusing Example Broadcaster branding, footage, or anchor likeness without authorization",
        "remediation_steps": [
            "Document evidence with full-screen screenshots and video recording",
            "Report via Instagram Help Center → Report → Impersonation or Copyright",
            "If using Example Broadcaster footage: file copyright report at instagram.com/legal/copyright",
            "Add account to Kaaval monitoring watchlist",
            "If account has large following: issue public clarification on official @examplebroadcaster",
            "Track if content spreads to other platforms",
        ],
        "who_to_contact": "Legal + Editorial Head",
        "legal_implications": "Copyright Act 1957 if footage used. Trademark Act 1999 if logo misused. IT Act 2000 S.66C if impersonation",
        "resolution_criteria": "Report filed + content removed or account actioned by Instagram + documented in audit trail",
    },
}

_RECYCLED_VERDICTS = ("RECYCLED", "MANIPULATED", "FAKE")


def _ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── GET /api/findings ─────────────────────────────────────
@router.get("")
@limiter.limit("60/minute")
def list_findings(request: Request):
    findings: list[dict] = []

    with Session(engine) as session:
        # 1. Kaaval AI — threats table
        threats = session.execute(
            select(Threat).order_by(Threat.detected_at.desc()).limit(50)
        ).scalars().all()

        for t in threats:
            findings.append({
                "id": str(t.id),
                "source": "kaaval",
                "title": t.title,
                "description": t.description,
                "severity": t.severity,
                "finding_type": t.threat_type,
                "timestamp": _ts(t.detected_at),
                "source_platform": t.source_platform,
                "status": t.status,
                "judge": t.judge,
            })

        # 2. Digital Kavasam — channel_snapshots where anomaly_detected=true
        snap_rows = session.execute(
            select(ChannelSnapshot, Channel)
            .join(Channel, ChannelSnapshot.channel_id == Channel.id)
            .where(ChannelSnapshot.anomaly_detected.is_(True))
            .order_by(ChannelSnapshot.scanned_at.desc())
            .limit(50)
        ).all()

        for snap, ch in snap_rows:
            findings.append({
                "id": str(snap.id),
                "source": "kavasam",
                "title": f"Anomaly: {snap.anomaly_type} on {ch.name}",
                "description": snap.anomaly_detail or snap.anomaly_type or "Channel anomaly detected",
                "severity": snap.anomaly_type or "HIGH",
                "finding_type": "CHANNEL_ANOMALY",
                "timestamp": _ts(snap.scanned_at),
                "source_platform": ch.platform,
                "status": "OPEN",
                "judge": snap.judge,
            })

        # 3. Veriff — fact_checks with negative verdicts
        checks = session.execute(
            select(FactCheck)
            .where(FactCheck.verdict.in_(_RECYCLED_VERDICTS))
            .order_by(FactCheck.checked_at.desc())
            .limit(50)
        ).scalars().all()

        for c in checks:
            claim_preview = (c.claim_text[:80] + "…") if len(c.claim_text) > 80 else c.claim_text
            severity = "HIGH" if c.verdict in ("MANIPULATED", "FAKE") else "MEDIUM"
            findings.append({
                "id": str(c.id),
                "source": "veriff",
                "title": f"{c.verdict}: {claim_preview}",
                "description": f"Verdict: {c.verdict} with {c.confidence:.0%} confidence",
                "severity": severity,
                "finding_type": "FACT_CHECK",
                "timestamp": _ts(c.checked_at),
                "source_platform": "veriff",
                "status": "RESOLVED",
                "judge": c.judge,
            })

    # Sort merged list newest-first, null timestamps last
    findings.sort(key=lambda f: f["timestamp"] or "", reverse=True)
    findings = findings[:50]


    by_severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    by_source = {"kaaval": 0, "kavasam": 0, "veriff": 0}
    for f in findings:
        sev = f["severity"]
        if sev in by_severity:
            by_severity[sev] += 1
        src = f["source"]
        if src in by_source:
            by_source[src] += 1

    return {
        "findings": findings,
        "total": len(findings),
        "by_severity": by_severity,
        "by_source": by_source,
    }


# Keyword → preferred playbook key for fuzzy fallback
_KEYWORD_MAP: list[tuple[str, str]] = [
    ("SUBSCRIBER",      "SUBSCRIBER_DROP"),
    ("VIDEO_COUNT_D",   "VIDEO_COUNT_DROP"),
    ("VIDEO_COUNT_S",   "VIDEO_COUNT_SPIKE"),
    ("VIDEO",           "VIDEO_COUNT_DROP"),
    ("CHANNEL_STATUS",  "CHANNEL_STATUS_CHANGE"),
    ("CHANNEL",         "CHANNEL_STATUS_CHANGE"),
    ("ANCHOR",          "ANCHOR_IMPERSONATION"),
    ("INSTAGRAM",       "INSTAGRAM_MISUSE"),
    ("BRIGADE",         "BRIGADE"),
    ("FAKE",            "FAKE_PAGE"),
    ("LOGO",            "LOGO_MISUSE"),
    ("FOOTAGE",         "FOOTAGE_THEFT"),
    ("DEEPFAKE",        "DEEPFAKE"),
    ("CERT",            "CERT_IN_INCIDENT"),
    ("IMPERSONATION",   "IMPERSONATION"),
    ("ARCHIVE",         "ARCHIVE_MISUSE"),
    ("RECYCLED",        "RECYCLED_FOOTAGE"),
]


def _fuzzy_playbook(key: str) -> tuple[dict, bool] | tuple[None, bool]:
    """Return (playbook, is_exact_match) or (None, False)."""
    upper = key.upper()
    # Exact match first
    if upper in _PLAYBOOKS:
        return _PLAYBOOKS[upper], True
    # Keyword scan (longest/most-specific keywords listed first in _KEYWORD_MAP)
    for keyword, playbook_key in _KEYWORD_MAP:
        if keyword in upper:
            return _PLAYBOOKS[playbook_key], False
    return None, False


# ── GET /api/findings/playbook/{threat_type} ──────────────
@router.get("/playbook/{threat_type}")
def get_playbook(threat_type: str):
    playbook, is_exact = _fuzzy_playbook(threat_type)
    if not playbook:
        raise HTTPException(
            status_code=404,
            detail=f"No playbook found for threat_type '{threat_type}'",
        )
    return {**playbook, "is_exact_match": is_exact}
