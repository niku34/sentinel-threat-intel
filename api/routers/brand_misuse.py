"""Brand Misuse console — unified logo misuse, footage theft & anchor
impersonation findings.

Human-in-the-loop by design. The platform NEVER auto-strikes, auto-takes-down
or auto-files. The DMCA lifecycle mirrors the CERT-In drafter:

    detected → (human confirms) → draft generated → (human files manually)

Storage note
------------
The `threats` table is owned by the `postgres` role, so this service cannot
ALTER it to add columns. The app role can INSERT/UPDATE rows, though, so the
Brand Misuse extras (detector confidence + DMCA draft state) live inside the
existing writable `judge` JSONB column under a namespaced `brand_misuse` key.
The `threats` table itself is the findings store.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.limiter import limiter
from models.channel import engine
from routers.kaaval import Threat

router = APIRouter(prefix="/api/brand-misuse", tags=["brand-misuse"])

# Threat types surfaced in the Brand Misuse console.
BRAND_MISUSE_TYPES = (
    "LOGO_MISUSE",
    "FOOTAGE_THEFT",
    "ANCHOR_IMPERSONATION",
    "FAKE_PAGE",
)

_RIGHTS_HOLDER = "Example Broadcaster"


def _ts(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _brand_state(threat: Threat) -> dict:
    """Extract the namespaced brand-misuse blob from the judge JSONB column."""
    judge = threat.judge or {}
    return judge.get("brand_misuse", {}) if isinstance(judge, dict) else {}


def _confidence(threat: Threat) -> float | None:
    state = _brand_state(threat)
    conf = state.get("confidence")
    return float(conf) if conf is not None else None


def _dmca_ready(threat: Threat) -> bool:
    return bool(_brand_state(threat).get("dmca_draft_ready", False))


def _build_dmca_draft(threat: Threat) -> str:
    """Deterministic DMCA notice from the finding — the CERT-In drafter pattern.

    This produces a *draft* only. A human must review and file it manually;
    the platform never sends this anywhere.
    """
    platform = threat.source_platform or "[Platform]"
    detected = _ts(threat.detected_at) or "[unknown]"
    source_url = threat.source_url or "[source URL not captured]"
    description = threat.description or threat.title

    return (
        f"To: {platform}\n"
        f"Re: Copyright Infringement Notice — {threat.threat_type}\n"
        f"\n"
        f"Content: {description}\n"
        f"Infringing URL: {source_url}\n"
        f"Original broadcast: {detected}\n"
        f"Rights holder: {_RIGHTS_HOLDER}\n"
        f"\n"
        f"Under the applicable copyright and safe-harbour provisions, the above "
        f"content reproduces material owned by {_RIGHTS_HOLDER} without licence "
        f"or authorisation.\n"
        f"\n"
        f"Action requested: Immediate removal of the infringing content.\n"
        f"\n"
        f"This notice is submitted in good faith. Contact: contact@example.com\n"
        f"\n"
        f"— DRAFT — Prepared for editorial review. Not yet filed."
    )


def _serialize(threat: Threat) -> dict:
    return {
        "finding_id": str(threat.id),
        "threat_type": threat.threat_type,
        "title": threat.title,
        "description": threat.description,
        "severity": threat.severity,
        "status": threat.status,
        "detected_at": _ts(threat.detected_at),
        "source_url": threat.source_url,
        "confidence": _confidence(threat),
        "dmca_draft_ready": _dmca_ready(threat),
    }


def _load(session: Session, finding_id: str) -> Threat:
    try:
        threat = session.get(Threat, finding_id)
    except Exception:
        threat = None
    if threat is None or threat.threat_type not in BRAND_MISUSE_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"Brand-misuse finding {finding_id} not found",
        )
    return threat


# ── GET /api/brand-misuse/findings ────────────────────────
@router.get("/findings")
@limiter.limit("60/minute")
def list_findings(request: Request) -> dict:
    with Session(engine) as session:
        threats = (
            session.execute(
                select(Threat)
                .where(Threat.threat_type.in_(BRAND_MISUSE_TYPES))
                .order_by(Threat.detected_at.desc())
                .limit(100)
            )
            .scalars()
            .all()
        )
        findings = [_serialize(t) for t in threats]

    by_type = {t: 0 for t in BRAND_MISUSE_TYPES}
    for f in findings:
        if f["threat_type"] in by_type:
            by_type[f["threat_type"]] += 1

    return {"findings": findings, "total": len(findings), "by_type": by_type}


# ── POST /api/brand-misuse/{finding_id}/confirm ───────────
@router.post("/{finding_id}/confirm")
@limiter.limit("30/minute")
def confirm_finding(request: Request, finding_id: str) -> dict:
    """Editorial confirms a finding is real.

    detected → confirmed, and the DMCA draft is generated (but NOT filed).
    """
    with Session(engine) as session:
        threat = _load(session, finding_id)

        threat.status = "confirmed"

        judge = dict(threat.judge) if isinstance(threat.judge, dict) else {}
        brand = dict(judge.get("brand_misuse", {}))
        brand["confidence"] = brand.get("confidence")
        brand["dmca_draft_ready"] = True
        brand["dmca_draft"] = _build_dmca_draft(threat)
        brand["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        judge["brand_misuse"] = brand
        # Reassign (not mutate) so SQLAlchemy flags the JSONB column dirty.
        threat.judge = judge

        session.commit()

        return {
            "finding_id": finding_id,
            "status": "confirmed",
            "dmca_draft_ready": True,
        }


# ── GET /api/brand-misuse/{finding_id}/dmca-draft ─────────
@router.get("/{finding_id}/dmca-draft")
def get_dmca_draft(finding_id: str) -> dict:
    with Session(engine) as session:
        threat = _load(session, finding_id)
        state = _brand_state(threat)

        if not state.get("dmca_draft_ready"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "DMCA draft not available — confirm the finding first "
                    "(human-in-the-loop required before any draft is generated)."
                ),
            )

        return {
            "finding_id": finding_id,
            "threat_type": threat.threat_type,
            "dmca_draft": state.get("dmca_draft") or _build_dmca_draft(threat),
            "rights_holder": _RIGHTS_HOLDER,
            "filed": False,
            "note": "Draft only. A human must review and file this manually.",
        }
