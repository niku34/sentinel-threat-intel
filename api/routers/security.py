import json as _json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from models.channel import engine
from routers.auth import active_sessions

router = APIRouter(prefix="/api/security", tags=["security"])

_CURRENT_USER_ID = "admin@example.com"


def _current_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


# ── GET /api/security/sessions ─────────────────────────────────────────────

@router.get("/sessions")
def list_sessions(request: Request):
    current_token = _current_token(request)
    result = []
    for token, s in active_sessions.items():
        result.append({
            **s,
            "is_current": token == current_token,
        })
    # Current session first, then sort by login_at descending
    result.sort(key=lambda s: (not s["is_current"], s.get("login_at", "")), reverse=False)
    result.sort(key=lambda s: s.get("login_at", ""), reverse=True)
    # Put current first regardless
    result.sort(key=lambda s: 0 if s["is_current"] else 1)
    return {"sessions": result, "total": len(result)}


# ── POST /api/security/sessions/{user_id}/force-logout ────────────────────

@router.post("/sessions/{user_id}/force-logout")
def force_logout(user_id: str, request: Request):
    current_token = _current_token(request)
    current_session = active_sessions.get(current_token) if current_token else None
    if current_session and current_session["user_id"] == user_id:
        raise HTTPException(403, detail="Cannot force-logout your own session")

    removed = 0
    tokens_to_remove = [t for t, s in active_sessions.items() if s["user_id"] == user_id]
    for t in tokens_to_remove:
        active_sessions.pop(t, None)
        removed += 1

    if removed == 0:
        raise HTTPException(404, detail="No active session found for that user")

    return {"message": "Session terminated", "removed": removed}


# ── GET /api/security/login-history ───────────────────────────────────────

@router.get("/login-history")
def login_history():
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT action, details_json, ip_address, created_at
                FROM audit_log
                WHERE action ILIKE 'login%' OR action ILIKE 'logout%'
                ORDER BY created_at DESC
                LIMIT 20
            """)).fetchall()
    except Exception:
        return {"history": []}

    history = []
    for r in rows:
        action = r.action
        if action == "login_failed":
            status = "BLOCKED"
        else:
            status = "OK"

        import json as _json
        try:
            details = _json.loads(r.details_json or "{}")
            user_label = details.get("user") or details.get("email") or "unknown"
        except Exception:
            user_label = "unknown"

        history.append({
            "user": user_label,
            "ip": r.ip_address or "unknown",
            "action": action,
            "status": status,
            "timestamp": r.created_at.isoformat() if r.created_at else None,
        })

    return {"history": history}


# ── GET /api/security/anomaly-log ─────────────────────────────────────────

@router.get("/anomaly-log")
def anomaly_log():
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT action, details_json, ip_address, created_at
                FROM audit_log
                WHERE action ILIKE '%ANOMALY%' OR action ILIKE '%SUSPICIOUS%'
                ORDER BY created_at DESC
                LIMIT 20
            """)).fetchall()
    except Exception:
        return {"events": []}

    events = []
    for r in rows:
        try:
            details = _json.loads(r.details_json or "{}")
        except Exception:
            details = {}
        action_taken = "sessions_revoked" if r.action == "ANOMALY_KILL_SWITCH" else "logged"
        events.append({
            "timestamp": r.created_at.isoformat() if r.created_at else None,
            "user": details.get("username") or details.get("user") or "unknown",
            "ip": r.ip_address or "unknown",
            "reasons": details.get("reasons", []),
            "risk_level": details.get("risk_level", "UNKNOWN"),
            "action": r.action,
            "action_taken": action_taken,
        })

    return {"events": events}


# ── GET /api/security/posture ──────────────────────────────────────────────

def _check(status: bool | None, verified: bool, note: str) -> dict:
    """One posture check.

    status   — does the control genuinely pass? None = not a system check.
    verified — was the answer produced by a real check against the running
               system? False means we could not confirm it either way, and the
               UI must not present it as a pass.
    """
    return {"status": status, "verified": verified, "note": note}


def _scalar(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar()


@router.get("/posture")
def security_posture(request: Request):
    """Compliance checklist, verified against the running system.

    Every entry is the result of a real check. Anything we cannot confirm is
    returned with verified=false and status=false — never assumed to pass.
    """
    checks: dict[str, dict] = {}

    # ── 1. Encryption at rest ──────────────────────────────────────────────
    # Column-level PII encryption (pgcrypto + bytea columns) is verifiable.
    # Disk / tablespace encryption is not visible from the application, and no
    # attestation marker is configured, so we do not claim it.
    try:
        with engine.connect() as conn:
            pgcrypto = _scalar(conn, "SELECT count(*) FROM pg_extension WHERE extname = 'pgcrypto'")
            enc_cols = _scalar(conn, """
                SELECT count(*) FROM information_schema.columns
                WHERE table_name = 'users' AND column_name IN ('email', 'mfa_secret')
                  AND data_type = 'bytea'
            """)
            attested = _scalar(conn, """
                SELECT value FROM platform_config
                WHERE module = 'security' AND key = 'encryption_at_rest_attested'
            """)
        if attested and str(attested).lower() in ("true", "1", "yes"):
            checks["encryption_at_rest"] = _check(
                True, True,
                f"Disk encryption attested in platform_config; pgcrypto installed={bool(pgcrypto)}, "
                f"{enc_cols} encrypted PII column(s).",
            )
        else:
            checks["encryption_at_rest"] = _check(
                False, False,
                f"Column-level PII encryption present (pgcrypto installed={bool(pgcrypto)}, "
                f"{enc_cols} bytea PII column(s)), but full-disk/tablespace encryption cannot be "
                "verified from the application and no attestation is recorded in platform_config.",
            )
    except Exception as e:
        checks["encryption_at_rest"] = _check(False, False, f"Check failed: {e}")

    # ── 2. Encryption in transit ───────────────────────────────────────────
    # Based on how this very request arrived.
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    if scheme == "https":
        checks["encryption_in_transit"] = _check(
            True, True, "This request was served over HTTPS.",
        )
    else:
        checks["encryption_in_transit"] = _check(
            False, True,
            f"API is being served over plain {scheme.upper()} — no TLS termination detected "
            "on this request.",
        )

    # ── 3. MFA ─────────────────────────────────────────────────────────────
    try:
        from routers.auth import MFA_ENFORCED
        if MFA_ENFORCED:
            checks["mfa_mandatory"] = _check(True, True, "Login flow enforces a second factor.")
        else:
            checks["mfa_mandatory"] = _check(
                False, True,
                "MFA not implemented — /api/auth/login is password-only. The users.mfa_secret "
                "column exists but is never read or validated.",
            )
    except Exception as e:
        checks["mfa_mandatory"] = _check(False, False, f"Check failed: {e}")

    # ── 4. Audit retention ─────────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            total = _scalar(conn, "SELECT count(*) FROM audit_log")
            oldest = _scalar(conn, "SELECT min(created_at) FROM audit_log")
            delete_blocked = _scalar(conn, """
                SELECT count(*) FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                WHERE c.relname = 'audit_log' AND t.tgname = 'audit_log_worm_trigger'
                  AND NOT t.tgisinternal
            """)
        span_days = None
        if oldest:
            from datetime import datetime as _dt, timezone as _tz
            span_days = (_dt.now(_tz.utc) - oldest).days
        if delete_blocked:
            checks["audit_retention_180d"] = _check(
                True, True,
                f"No purge path exists: the WORM trigger blocks DELETE/UPDATE on audit_log, so "
                f"records are retained indefinitely. {total} record(s), oldest {span_days} day(s) old.",
            )
        else:
            checks["audit_retention_180d"] = _check(
                False, True,
                f"No immutability trigger on audit_log — retention is not enforced. "
                f"{total} record(s), oldest {span_days} day(s) old.",
            )
    except Exception as e:
        checks["audit_retention_180d"] = _check(False, False, f"Check failed: {e}")

    # ── 5. Tamper-proof logging ────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            worm = _scalar(conn, """
                SELECT count(*) FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                WHERE c.relname = 'audit_log' AND t.tgname = 'audit_log_worm_trigger'
                  AND NOT t.tgisinternal
            """)
        checks["tamper_proof_logging"] = _check(
            bool(worm), True,
            "audit_log_worm_trigger present on audit_log — UPDATE and DELETE are rejected."
            if worm else "audit_log_worm_trigger not found — audit rows can be modified.",
        )
    except Exception as e:
        checks["tamper_proof_logging"] = _check(False, False, f"Check failed: {e}")

    # ── 6. API key rotation ────────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            rotations = conn.execute(text("""
                SELECT key, value FROM platform_config
                WHERE key ILIKE '%rotated_at%' OR key ILIKE '%rotation%'
            """)).fetchall()
        if rotations:
            checks["api_key_rotation"] = _check(
                True, True,
                f"{len(rotations)} rotation timestamp(s) recorded in platform_config.",
            )
        else:
            checks["api_key_rotation"] = _check(
                False, True,
                "No rotation timestamps recorded in platform_config — API keys have never been "
                "rotated and no rotation schedule is tracked.",
            )
    except Exception as e:
        checks["api_key_rotation"] = _check(False, False, f"Check failed: {e}")

    # ── 7. RBAC ────────────────────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            policies = _scalar(conn, "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")
            db_roles = conn.execute(text(
                "SELECT rolname FROM pg_roles WHERE rolname LIKE 'sentinel%'"
            )).scalars().all()
            role_ck = _scalar(conn, """
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conname = 'users_role_check'
            """)
        app_roles = []
        if role_ck:
            import re
            app_roles = re.findall(r"'([a-z_]+)'::text", role_ck)
        ok = bool(policies) and len(db_roles) > 1 and len(app_roles) > 1
        checks["rbac"] = _check(
            ok, True,
            f"{policies} row-level security policy/policies across {len(db_roles)} database role(s) "
            f"({', '.join(db_roles)}); users.role constrained to {len(app_roles)} role(s): "
            f"{', '.join(app_roles)}. Note: per-endpoint role enforcement is not yet applied in the "
            "API layer — access control is currently enforced at the database level.",
        )
    except Exception as e:
        checks["rbac"] = _check(False, False, f"Check failed: {e}")

    # ── 8. Penetration test ────────────────────────────────────────────────
    # Not a system property. Reported only if an external record exists.
    try:
        with engine.connect() as conn:
            pentest = _scalar(conn, """
                SELECT value FROM platform_config
                WHERE module = 'security' AND key = 'pentest_scheduled'
            """)
    except Exception:
        pentest = None
    checks["pentest_scheduled"] = _check(
        None, False,
        f"Tracked externally, not a system check. platform_config record: {pentest}"
        if pentest else
        "NOT_APPLICABLE — penetration testing is tracked outside the platform; no record configured "
        "in platform_config.",
    )

    return checks
