from typing import Dict

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.channel import engine

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/{module}")
def get_config(module: str):
    with Session(engine) as db:
        try:
            rows = db.execute(
                text("SELECT key, value FROM platform_config WHERE module = :module"),
                {"module": module},
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        except Exception:
            return {}


@router.post("/{module}")
def save_config(module: str, body: Dict[str, str] = Body(...)):
    if not body:
        return {"saved": True, "module": module, "keys_updated": 0}

    with Session(engine) as db:
        try:
            for key, value in body.items():
                db.execute(
                    text(
                        "INSERT INTO platform_config (module, key, value, updated_at) "
                        "VALUES (:module, :key, :value, NOW()) "
                        "ON CONFLICT (module, key) DO UPDATE "
                        "SET value = EXCLUDED.value, updated_at = NOW()"
                    ),
                    {"module": module, "key": key, "value": str(value)},
                )
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(exc))

    return {"saved": True, "module": module, "keys_updated": len(body)}
