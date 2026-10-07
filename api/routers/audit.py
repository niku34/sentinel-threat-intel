from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import text

from core.audit_hash import actor_from_details, audit_entry_hash
from models.channel import engine

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _conn():
    return engine.connect()


# ── GET /api/audit ────────────────────────────────────────
@router.get("")
def list_events(
    limit: int = Query(default=50, ge=1, le=500),
    module: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
):
    conditions = ["1=1"]
    params: dict = {"limit": limit}

    if module:
        conditions.append("module ILIKE :module")
        params["module"] = f"%{module}%"
    if action:
        conditions.append("action ILIKE :action")
        params["action"] = f"%{action}%"

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT id, action, module, details_json, created_at
        FROM audit_log
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT :limit
    """)

    count_sql = text(f"""
        SELECT COUNT(*) FROM audit_log WHERE {where}
    """)

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute(count_sql, {k: v for k, v in params.items() if k != "limit"}).scalar_one()

    events = [
        {
            "id": str(row.id),
            "action": row.action,
            "module": row.module,
            "details_json": row.details_json,
            "ip_address": None,  # encrypted BYTEA — not exposed
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]

    return {"events": events, "total": total}


# ── GET /api/audit/metrics ────────────────────────────────
@router.get("/metrics")
def get_metrics():
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    since_90d = datetime.now(timezone.utc) - timedelta(days=90)

    with _conn() as conn:
        events_24h = conn.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE created_at >= :since"),
            {"since": since_24h},
        ).scalar_one()

        cert_in_drafts = conn.execute(
            text(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE module = 'cert_in' AND created_at >= :since"
            ),
            {"since": since_90d},
        ).scalar_one()

        oldest = conn.execute(
            text("SELECT MIN(created_at) FROM audit_log")
        ).scalar()

    retained_days = 0
    if oldest is not None:
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        retained_days = (datetime.now(timezone.utc) - oldest).days

    # Tamper state comes from the real hash-chain walk, not a literal. This
    # previously returned "PASS" unconditionally while verify_integrity() — the
    # function that actually checks — sat unused in the same file.
    integrity = verify_integrity()
    hashed = int(integrity["hashed_entries"])
    total = int(integrity["total_entries"])

    # Coverage is measured from the moment the chain went live, not from the
    # first entry ever written. Entries predating the chain can never be hashed
    # — the WORM trigger makes them undeletable and backfilling them would
    # fabricate a chain of custody that never existed — so a raw hashed/total
    # ratio would report PARTIAL permanently and tell you nothing about whether
    # the chain is currently working.
    with _conn() as conn:
        chain_started_at = conn.execute(
            text("SELECT MIN(created_at) FROM audit_log WHERE entry_hash IS NOT NULL")
        ).scalar()
        since_chain_total = 0
        since_chain_hashed = 0
        if chain_started_at is not None:
            since_chain_total = conn.execute(
                text("SELECT COUNT(*) FROM audit_log WHERE created_at >= :s"),
                {"s": chain_started_at},
            ).scalar_one()
            since_chain_hashed = conn.execute(
                text(
                    "SELECT COUNT(*) FROM audit_log "
                    "WHERE created_at >= :s AND entry_hash IS NOT NULL"
                ),
                {"s": chain_started_at},
            ).scalar_one()

    unchained_since_start = since_chain_total - since_chain_hashed

    if integrity["valid"] is False:
        tamper_check = "FAIL"
    elif integrity["valid"] is None or hashed == 0:
        tamper_check = "NOT_ACTIVE"
    elif unchained_since_start > 0:
        # Entries written AFTER the chain went live are missing hashes. That is
        # a defect in a writer, not an artefact of history.
        tamper_check = "PARTIAL"
    else:
        tamper_check = "PASS"

    if tamper_check == "PARTIAL":
        coverage_note = (
            f"{unchained_since_start} entr"
            f"{'y' if unchained_since_start == 1 else 'ies'} written since the "
            f"chain went live carry no hash — a writer is not chaining. "
            f"Verified {since_chain_hashed}/{since_chain_total} since "
            f"{chain_started_at.date()}; {total - hashed} older pre-chain "
            f"entries are outside the chain by design and are not backfilled."
        )
    elif tamper_check == "PASS":
        coverage_note = (
            f"Every entry written since the chain went live on "
            f"{chain_started_at.date()} is hashed and verifies "
            f"({since_chain_hashed}/{since_chain_total}). "
            f"{total - hashed} older pre-chain entries are outside the chain by "
            f"design and are not backfilled."
        )
    else:
        coverage_note = integrity["message"]

    return {
        "events_24h": events_24h,
        # Retention is enforced by WORM immutability (no delete path), not by a
        # 180-day policy job. Reported as the real retained span.
        "retention_days": retained_days,
        "retention_basis": (
            "WORM trigger blocks DELETE/UPDATE — entries are retained indefinitely"
            if integrity["valid"] is not None
            else "Hash chain not active — retention not enforced"
        ),
        "tamper_check": tamper_check,
        "tamper_detail": coverage_note,
        "hashed_entries": hashed,
        "total_entries": total,
        "chain_started_at": chain_started_at.isoformat() if chain_started_at else None,
        "chained_since_start": since_chain_hashed,
        "entries_since_start": since_chain_total,
        "unchained_since_start": unchained_since_start,
        "cert_in_drafts": int(cert_in_drafts or 0),
    }


# ── GET /api/audit/verify-integrity ──────────────────────
@router.get("/verify-integrity")
def verify_integrity():
    """Walk the SHA-256 hash chain and report any tampered entries."""
    try:
        with _conn() as conn:
            rows = conn.execute(text("""
                SELECT id, user_id, action, module, details_json, created_at,
                       entry_hash, previous_hash
                FROM audit_log
                WHERE entry_hash IS NOT NULL
                ORDER BY created_at ASC
            """)).fetchall()
            total_all = conn.execute(text("SELECT COUNT(*) FROM audit_log")).scalar_one()
    except Exception:
        # Migration 010 not yet applied — hash chain columns don't exist
        with _conn() as conn:
            total_all = conn.execute(text("SELECT COUNT(*) FROM audit_log")).scalar_one()
        return {
            "valid": None,
            "total_entries": int(total_all),
            "hashed_entries": 0,
            "tampered_entries": 0,
            "first_tampered_at": None,
            "message": "Hash chain not active — run migration 010_worm_audit_log.sql to enable WORM protection",
        }

    if not rows:
        return {
            "valid": True,
            "total_entries": int(total_all),
            "hashed_entries": 0,
            "tampered_entries": 0,
            "first_tampered_at": None,
            "message": "No hashed entries yet — chain will start on next audit write",
        }

    tampered: list[dict] = []
    running_hash = "GENESIS"

    for row in rows:
        # Determine what the hash should be given the stored fields
        user = actor_from_details(row.details_json)

        expected = audit_entry_hash(
            running_hash, row.created_at, user, row.action, row.details_json
        )

        if expected != row.entry_hash:
            tampered.append({
                "id": str(row.id),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "stored_hash": row.entry_hash,
                "expected_hash": expected,
            })

        # Advance chain using stored hash so we propagate rather than cascade errors
        running_hash = row.entry_hash

    valid = len(tampered) == 0
    first_tampered_at = tampered[0]["created_at"] if tampered else None
    first_tampered_id = tampered[0]["id"] if tampered else None

    return {
        "valid": valid,
        "total_entries": int(total_all),
        "hashed_entries": len(rows),
        "tampered_entries": len(tampered),
        "first_tampered_at": first_tampered_at,
        "message": (
            "Audit chain intact"
            if valid
            else f"TAMPERING DETECTED at entry {first_tampered_id}"
        ),
    }
