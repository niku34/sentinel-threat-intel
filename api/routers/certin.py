import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.limiter import limiter
from models.cert_in_draft import CertInDraft
from models.channel import engine

router = APIRouter(prefix="/api/certin", tags=["certin"])


class CertInDraftRequest(BaseModel):
    incident_type: str = Field(..., min_length=1)
    affected_system: str = Field(..., min_length=1)
    detected_at: str = Field(..., description="ISO-8601 timestamp")
    description: str = Field(..., min_length=1)
    impact: str = Field(..., min_length=1)
    reported_by: str = Field(..., min_length=1)


def _parse_iso(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"detected_at must be ISO-8601: {exc}",
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _new_reference_number(detected_at: datetime) -> str:
    return (
        f"{settings.certin_reference_prefix}-"
        f"{detected_at.strftime('%Y%m%d')}-"
        f"{random.randint(0, 9999):04d}"
    )


def _build_report(
    body: CertInDraftRequest,
    detected_at: datetime,
    reference_number: str,
) -> dict:
    now = datetime.now(timezone.utc)
    deadline = detected_at + timedelta(hours=settings.certin_deadline_hours)
    deadline_status = "WITHIN_WINDOW" if now <= deadline else "DEADLINE_PASSED"
    minutes_remaining = int((deadline - now).total_seconds() // 60)

    return {
        "reference_number": reference_number,
        "report_generated_at": now.isoformat(),
        "reporting_to": settings.certin_reporting_email,
        "regulation": "CERT-In Directions, 28 April 2022 — 6-hour reporting rule",
        "deadline": {
            "deadline_at": deadline.isoformat(),
            "deadline_hours": settings.certin_deadline_hours,
            "status": deadline_status,
            "minutes_remaining": minutes_remaining,
        },
        "organisation": {
            "name": settings.certin_organisation,
            "contact_email": settings.certin_contact_email,
            "point_of_contact": body.reported_by,
        },
        "incident": {
            "type": body.incident_type,
            "affected_system": body.affected_system,
            "detected_at": detected_at.isoformat(),
            "description": body.description,
            "impact": body.impact,
        },
        "mandatory_fields": {
            "nature_of_incident": body.incident_type,
            "service_affected": body.affected_system,
            "date_and_time_of_occurrence": detected_at.isoformat(),
            "date_and_time_of_detection": detected_at.isoformat(),
            "brief_description": body.description,
            "impact_on_organisation": body.impact,
            "actions_taken": "Initial containment in progress; full draft below.",
            "remedial_actions_planned": (
                "Forensic preservation, password rotation, "
                "access-log audit, public clarification if reputational impact."
            ),
        },
        "status": "DRAFT",
    }


def _serialize_draft(draft: CertInDraft) -> dict:
    return {
        "id": str(draft.id),
        "reference_number": draft.reference_number,
        "incident_type": draft.incident_type,
        "affected_system": draft.affected_system,
        "detected_at": draft.detected_at.isoformat(),
        "description": draft.description,
        "impact": draft.impact,
        "reported_by": draft.reported_by,
        "report": draft.report_json,
        "status": draft.status,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
    }


@router.post("/draft", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_draft(request: Request, body: CertInDraftRequest) -> dict:
    detected_at = _parse_iso(body.detected_at)
    reference_number = _new_reference_number(detected_at)
    report = _build_report(body, detected_at, reference_number)

    with Session(engine) as session:
        draft = CertInDraft(
            reference_number=reference_number,
            incident_type=body.incident_type,
            affected_system=body.affected_system,
            detected_at=detected_at,
            description=body.description,
            impact=body.impact,
            reported_by=body.reported_by,
            report_json=report,
            status="DRAFT",
        )
        session.add(draft)
        session.commit()
        session.refresh(draft)
        return _serialize_draft(draft)


@router.get("/drafts")
def list_drafts() -> dict:
    with Session(engine) as session:
        drafts = (
            session.execute(
                select(CertInDraft).order_by(CertInDraft.created_at.desc())
            )
            .scalars()
            .all()
        )
        return {"drafts": [_serialize_draft(d) for d in drafts]}


@router.get("/drafts/{reference_number}")
def get_draft(reference_number: str) -> dict:
    with Session(engine) as session:
        draft = session.execute(
            select(CertInDraft).where(
                CertInDraft.reference_number == reference_number
            )
        ).scalar_one_or_none()
        if draft is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"CERT-In draft {reference_number} not found",
            )
        return _serialize_draft(draft)
