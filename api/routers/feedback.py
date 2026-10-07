from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.channel import engine

router = APIRouter(tags=["feedback"])

# Minimum editorial decisions before a false-positive rate is reported at all.
MIN_FEEDBACK_SAMPLE = 20


class FeedbackBody(BaseModel):
    verdict: str
    reason: str | None = None


@router.post("/api/findings/{finding_id}/feedback")
def submit_feedback(finding_id: str, body: FeedbackBody):
    if body.verdict not in ("confirm", "dismiss"):
        raise HTTPException(
            status_code=400, detail="verdict must be 'confirm' or 'dismiss'"
        )

    with Session(engine) as db:
        try:
            db.execute(
                text(
                    "INSERT INTO judge_feedback (finding_id, verdict, dismissed_reason) "
                    "VALUES (:finding_id, :verdict, :reason)"
                ),
                {
                    "finding_id": finding_id,
                    "verdict": body.verdict,
                    "reason": body.reason,
                },
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(exc))

    return {"ok": True, "finding_id": finding_id, "verdict": body.verdict}


@router.get("/api/judge/feedback-summary")
def feedback_summary():
    with Session(engine) as db:
        try:
            confirmed = db.execute(
                text("SELECT COUNT(*) FROM judge_feedback WHERE verdict = 'confirm'")
            ).scalar() or 0
            dismissed = db.execute(
                text("SELECT COUNT(*) FROM judge_feedback WHERE verdict = 'dismiss'")
            ).scalar() or 0
            total = int(confirmed) + int(dismissed)
            # A ratio over a handful of samples is not a rate. Below the
            # minimum sample size we return null rather than a figure like
            # "0.0" that reads as "no false positives observed".
            fp_rate = (
                round(int(dismissed) / total, 3)
                if total >= MIN_FEEDBACK_SAMPLE
                else None
            )

            recent_rows = db.execute(
                text(
                    "SELECT dismissed_reason FROM judge_feedback "
                    "WHERE verdict = 'dismiss' AND dismissed_reason IS NOT NULL "
                    "ORDER BY submitted_at DESC LIMIT 5"
                )
            ).fetchall()
            recent_dismissals = [row[0] for row in recent_rows]
        except Exception:
            return {
                "total_confirmed": 0,
                "total_dismissed": 0,
                "false_positive_rate": None,
                "sample_size": 0,
                "sufficient_sample": False,
                "note": "Feedback summary unavailable.",
                "recent_dismissals": [],
            }

    sufficient = total >= MIN_FEEDBACK_SAMPLE
    return {
        "total_confirmed": int(confirmed),
        "total_dismissed": int(dismissed),
        "false_positive_rate": fp_rate,
        "sample_size": total,
        "sufficient_sample": sufficient,
        "note": (
            f"Measured over {total} editorial decisions."
            if sufficient
            else f"Insufficient data — {total} editorial decision(s) recorded, "
                 f"{MIN_FEEDBACK_SAMPLE} needed before a rate is meaningful."
        ),
        "recent_dismissals": recent_dismissals,
    }
