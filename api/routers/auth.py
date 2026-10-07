import os
import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import text

from core.audit_hash import audit_entry_hash
from core.auth import create_access_token, decode_access_token, verify_password, hash_password
from core.limiter import limiter
from models.channel import engine

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer()

# Second-factor authentication is NOT implemented: /api/auth/login verifies a
# password only. The users.mfa_secret column exists in the schema but nothing
# reads or validates it. /api/security/posture reads this flag so the
# compliance checklist reports the real state instead of assuming MFA is on.
MFA_ENFORCED = False

# ── Active session store ───────────────────────────────────────────────────
# {token: {user_id, name, role, email, ip, device, browser, login_at}}
active_sessions: dict[str, dict] = {}


def _parse_ua(ua: str) -> tuple[str, str]:
    """Return (device, browser) from User-Agent string."""
    ua_lower = ua.lower()
    if "iphone" in ua_lower or "ipad" in ua_lower:
        device = "iOS"
        browser = "Safari"
    elif "android" in ua_lower:
        device = "Android"
        browser = "Chrome" if "chrome" in ua_lower else "Browser"
    else:
        device = "Desktop"
        if "edg" in ua_lower:
            browser = "Edge"
        elif "firefox" in ua_lower:
            browser = "Firefox"
        elif "chrome" in ua_lower:
            browser = "Chrome"
        elif "safari" in ua_lower:
            browser = "Safari"
        else:
            browser = "Browser"
    return device, browser


_IST = timezone(timedelta(hours=5, minutes=30))


def detect_login_anomaly(username: str, ip: str, user_agent: str) -> dict:
    """Inspect active_sessions to flag suspicious login patterns before the
    new session is added to the store."""
    existing = [s for s in active_sessions.values() if s["user_id"] == username]

    known_ips = {s["ip"] for s in existing}
    new_ip = bool(known_ips) and ip not in known_ips

    hour_ist = datetime.now(_IST).hour
    unusual_hour = hour_ist < 6 or hour_ist >= 23

    session_flood = len(existing) >= 3

    reasons: list[str] = []
    if new_ip:
        reasons.append(f"Login from new IP {ip} (known: {', '.join(sorted(known_ips))})")
    if unusual_hour:
        reasons.append(f"Login at unusual hour {hour_ist:02d}:xx IST (outside 06:00–23:00)")
    if session_flood:
        reasons.append(f"Session flood — {len(existing)} concurrent sessions already active")

    if session_flood:
        risk_level = "CRITICAL"
    elif new_ip and unusual_hour:
        risk_level = "HIGH"
    elif new_ip or unusual_hour:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "anomaly_detected": bool(reasons),
        "reasons": reasons,
        "risk_level": risk_level,
        "flags": {"new_ip": new_ip, "unusual_hour": unusual_hour, "session_flood": session_flood},
    }


def _audit_hash(previous_hash: str, timestamp, user: str, action: str, details: str) -> str:
    """Delegates to the shared canonical implementation.

    Kept as a thin wrapper so the three writers (this module, the fn_audit_log
    DB trigger, and the verifier in routers/audit.py) cannot drift apart.
    """
    return audit_entry_hash(previous_hash, timestamp, user, action, details)


def _last_audit_hash(conn) -> str:
    row = conn.execute(text(
        "SELECT entry_hash FROM audit_log WHERE entry_hash IS NOT NULL ORDER BY created_at DESC LIMIT 1"
    )).fetchone()
    return row.entry_hash if row else "GENESIS"


def _write_login_audit(email: str, name: str, ip: str | None, action: str) -> None:
    now = datetime.now(timezone.utc)
    details = json.dumps({"user": name, "email": email})
    try:
        with engine.begin() as conn:
            prev = _last_audit_hash(conn)
            entry_hash = _audit_hash(prev, now, name, action, details)
            conn.execute(text("""
                INSERT INTO audit_log
                  (id, user_id, action, module, details_json, ip_address, created_at, entry_hash, previous_hash)
                VALUES (gen_random_uuid(), NULL, :action, 'auth',
                        :details, :ip, :now, :entry_hash, :previous_hash)
            """), {
                "action": action, "details": details, "ip": ip or "unknown",
                "now": now, "entry_hash": entry_hash, "previous_hash": prev,
            })
    except Exception:
        # Fallback: migration 010 not yet applied — insert without hash chain
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO audit_log (id, user_id, action, module, details_json, ip_address, created_at)
                    VALUES (gen_random_uuid(), NULL, :action, 'auth', :details, :ip, :now)
                """), {"action": action, "details": details, "ip": ip or "unknown", "now": now})
        except Exception:
            pass  # audit write failure must never break login


def _write_anomaly_audit(username: str, ip: str | None, action: str, anomaly: dict) -> None:
    now = datetime.now(timezone.utc)
    details = json.dumps({
        "username": username,
        "reasons": anomaly["reasons"],
        "risk_level": anomaly["risk_level"],
        "flags": anomaly.get("flags", {}),
    })
    try:
        with engine.begin() as conn:
            prev = _last_audit_hash(conn)
            entry_hash = _audit_hash(prev, now, username, action, details)
            conn.execute(text("""
                INSERT INTO audit_log
                  (id, user_id, action, module, details_json, ip_address, created_at, entry_hash, previous_hash)
                VALUES (gen_random_uuid(), NULL, :action, 'security',
                        :details, :ip, :now, :entry_hash, :previous_hash)
            """), {
                "action": action, "details": details, "ip": ip or "unknown",
                "now": now, "entry_hash": entry_hash, "previous_hash": prev,
            })
    except Exception:
        # Fallback: migration 010 not yet applied — insert without hash chain
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO audit_log (id, user_id, action, module, details_json, ip_address, created_at)
                    VALUES (gen_random_uuid(), NULL, :action, 'security', :details, :ip, :now)
                """), {"action": action, "details": details, "ip": ip or "unknown", "now": now})
        except Exception:
            pass

import secrets as _secrets


def _bootstrap_password(env_name: str) -> str:
    """Bootstrap credential comes from the environment; if unset, a random
    one is generated so no account is ever left with a guessable default."""
    return os.getenv(env_name) or _secrets.token_urlsafe(24)


# Hardcoded users — replaced with DB lookup once user model is wired up.
# Passwords stored as bcrypt hashes; generated once at module load so startup
# cost is paid only at cold start, not per request.
#
# NOTE (2026-08-16): contact@example.com's password was rotated off the guessable
# "<removed>" default, and contact@example.com was added — both to a random
# 20-char credential, shared with the account holder out of band (not
# committed anywhere in plaintext). See infrastructure notes for the
# rotation record. The `users` DB table is unrelated to this dict: it's
# empty, pgcrypto-encrypted, and RLS-blocked for sentinel_app — only
# entries in this dict can actually authenticate (see api/routers/users.py's
# module docstring for the same caveat on the team_members.json roster).
_USERS = {
    "admin@example.com": {
        "name": "Nitin Pranav",
        "role": "super_admin",
        "hashed_password": hash_password(_bootstrap_password("ADMIN_PASSWORD")),
    },
    "contact@example.com": {
        "name": "CEO",
        "role": "super_admin",
        "hashed_password": hash_password(_bootstrap_password("DEMO_USER_PASSWORD")),
    },
    "contact@example.com": {
        "name": "IT Admin",
        "role": "super_admin",
        "hashed_password": hash_password(_bootstrap_password("DEMO_ADMIN2_PASSWORD")),
    },
}


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    name: str


class MeResponse(BaseModel):
    email: str
    role: str
    name: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


_401 = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


# ── POST /api/auth/login ──────────────────────────────────
@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest):
    from websocket_manager import emit_security_alert

    user = _USERS.get(body.email)
    if not user or not verify_password(body.password, user["hashed_password"]):
        _write_login_audit(body.email, body.email, request.client.host if request.client else None, "login_failed")
        raise _401

    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")

    # Detect anomaly BEFORE adding the new session so counts are accurate.
    anomaly = detect_login_anomaly(body.email, ip, ua)

    token = create_access_token(
        {"sub": body.email, "role": user["role"], "name": user["name"]}
    )
    device, browser = _parse_ua(ua)
    active_sessions[token] = {
        "user_id": body.email,
        "name": user["name"],
        "role": user["role"],
        "email": body.email,
        "ip": ip,
        "device": device,
        "browser": browser,
        "login_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_login_audit(body.email, user["name"], ip, "login_success")

    # ── Anomaly fast-path ─────────────────────────────────────────────────
    warning: str | None = None
    if anomaly["risk_level"] == "CRITICAL":
        # Revoke all OTHER sessions for this user immediately (keep new token).
        tokens_to_revoke = [
            t for t, s in list(active_sessions.items())
            if s["user_id"] == body.email and t != token
        ]
        for t in tokens_to_revoke:
            active_sessions.pop(t, None)

        _write_anomaly_audit(body.email, ip, "ANOMALY_KILL_SWITCH", anomaly)
        emit_security_alert("login_anomaly_kill_switch", body.email, anomaly["reasons"], "CRITICAL")
        warning = "Anomalous login detected — other sessions terminated"

    elif anomaly["risk_level"] in ("HIGH", "MEDIUM"):
        _write_anomaly_audit(body.email, ip, "SUSPICIOUS_LOGIN", anomaly)
        emit_security_alert("suspicious_login", body.email, anomaly["reasons"], anomaly["risk_level"])
        warning = f"Suspicious login detected ({anomaly['risk_level']})"

    response: dict = {
        "access_token": token,
        "token_type": "bearer",
        "role": user["role"],
        "name": user["name"],
    }
    if warning:
        response["warning"] = warning
        response["anomaly"] = anomaly
    return response


# ── GET /api/auth/me ──────────────────────────────────────
@router.get("/me", response_model=MeResponse)
def me(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise _401
    email = payload["sub"]
    user = _USERS.get(email)
    if not user:
        raise _401
    return MeResponse(email=email, role=user["role"], name=user["name"])


# ── POST /api/auth/logout ─────────────────────────────────
@router.post("/logout")
def logout(request: Request, credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    token = credentials.credentials
    session = active_sessions.pop(token, None)
    if session:
        _write_login_audit(session["email"], session["name"],
                           request.client.host if request.client else None, "logout")
    return {"message": "Logged out successfully"}


# ── POST /api/auth/change-password ────────────────────────
@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
):
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise _401
    email = payload["sub"]
    user = _USERS.get(email)
    if not user or not verify_password(body.current_password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Current password incorrect")
    _USERS[email]["hashed_password"] = hash_password(body.new_password)
    return {"message": "Password changed successfully"}
