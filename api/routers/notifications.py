"""
Unified notifications from all modules.

Tables found in DB:
  threats          → KAAVAL (CRITICAL/HIGH, last 7d)
  channel_snapshots → KAVASAM (anomaly_detected, last 7d)
  fact_checks      → VERIFF  (actionable verdicts, last 7d)
  scans            → SYSTEM  (completed, last 7d)
  audit_log        → ADMIN   (channel/user/config changes, last 7d)
                   → SECURITY (login failures, last 24h)

Tables skipped (not in schema): anomalies, login_events, veriff_checks
"""

from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.channel import engine

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_read_ids: set[str] = set()

_ADMIN_ACTIONS = {
    "api_key_rotation_due",
    "user_added",
    "user_removed",
    "role_changed",
    "kill_switch_triggered",
    "config_changed",
    "password_changed",
}

_SECURITY_ACTIONS = {
    "login_failed",
    "logout_forced",
    "new_device_login",
    "suspicious_access",
}

_VERIFF_ACTIONABLE = ("RECYCLED", "FAKE", "MANIPULATED", "NEEDS_HUMAN_REVIEW")


def _ts(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


def _fetch_kaaval(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT id::text, title, threat_type, severity, detected_at
        FROM threats
        WHERE severity IN ('CRITICAL', 'HIGH')
          AND detected_at >= NOW() - INTERVAL '7 days'
          AND status NOT IN ('resolved', 'dismissed')
        ORDER BY detected_at DESC
        LIMIT 15
    """)).fetchall()
    return [
        {
            "id": f"kaaval:{r.id}",
            "source": "KAAVAL",
            "title": r.title,
            "subtitle": r.threat_type.replace("_", " "),
            "severity": r.severity,
            "detected_at": _ts(r.detected_at),
        }
        for r in rows
    ]


def _fetch_kavasam(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT cs.id::text,
               'Anomaly on ' || ch.name                AS title,
               COALESCE(cs.anomaly_type, 'UNKNOWN')    AS subtitle,
               cs.scanned_at
        FROM channel_snapshots cs
        JOIN channels ch ON cs.channel_id = ch.id
        WHERE cs.anomaly_detected = true
          AND cs.scanned_at >= NOW() - INTERVAL '7 days'
        ORDER BY cs.scanned_at DESC
        LIMIT 10
    """)).fetchall()
    return [
        {
            "id": f"kavasam:{r.id}",
            "source": "KAVASAM",
            "title": r.title,
            "subtitle": r.subtitle.replace("_", " "),
            "severity": "HIGH",
            "detected_at": _ts(r.scanned_at),
        }
        for r in rows
    ]


def _fetch_veriff(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT id::text,
               CASE WHEN LENGTH(claim_text) > 70
                    THEN LEFT(claim_text, 70) || '…'
                    ELSE claim_text END  AS title,
               verdict,
               checked_at
        FROM fact_checks
        WHERE verdict IN ('RECYCLED', 'FAKE', 'MANIPULATED', 'NEEDS_HUMAN_REVIEW')
          AND checked_at >= NOW() - INTERVAL '7 days'
        ORDER BY checked_at DESC
        LIMIT 10
    """)).fetchall()
    sev = {
        "FAKE": "HIGH",
        "MANIPULATED": "CRITICAL",
        "RECYCLED": "MEDIUM",
        "NEEDS_HUMAN_REVIEW": "MEDIUM",
    }
    return [
        {
            "id": f"veriff:{r.id}",
            "source": "VERIFF",
            "title": r.title,
            "subtitle": r.verdict.replace("_", " "),
            "severity": sev.get(r.verdict, "MEDIUM"),
            "detected_at": _ts(r.checked_at),
        }
        for r in rows
    ]


def _fetch_system(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT s.id::text,
               INITCAP(s.scan_type) || ' scan completed'            AS title,
               s.findings_count::text || ' finding(s) detected'     AS subtitle,
               s.completed_at
        FROM scans s
        WHERE s.status = 'completed'
          AND s.completed_at >= NOW() - INTERVAL '7 days'
        ORDER BY s.completed_at DESC
        LIMIT 5
    """)).fetchall()
    return [
        {
            "id": f"system:{r.id}",
            "source": "SYSTEM",
            "title": r.title,
            "subtitle": r.subtitle,
            "severity": "LOW",
            "detected_at": _ts(r.completed_at),
        }
        for r in rows
    ]


def _fetch_admin(conn) -> list[dict]:
    # Actionable admin events: channel/user/config changes and explicit admin actions
    rows = conn.execute(text("""
        SELECT id::text, action, module, created_at
        FROM audit_log
        WHERE (
            module IN ('channels', 'users', 'platform_config', 'api_keys')
            OR action = ANY(:actions)
        )
          AND created_at >= NOW() - INTERVAL '7 days'
        ORDER BY created_at DESC
        LIMIT 10
    """), {"actions": list(_ADMIN_ACTIONS)}).fetchall()

    _label = {
        "INSERT": {"channels": "New channel registered", "users": "New user added",
                   "api_keys": "API key created"},
        "UPDATE": {"platform_config": "Platform config changed", "channels": "Channel updated",
                   "users": "User updated"},
        "DELETE": {"channels": "Channel removed", "users": "User removed"},
    }
    results = []
    for r in rows:
        title = (_label.get(r.action, {}).get(r.module)
                 or r.action.replace("_", " ").title())
        results.append({
            "id": f"admin:{r.id}",
            "source": "ADMIN",
            "title": title,
            "subtitle": f"{r.action} · {r.module}",
            "severity": "LOW",
            "detected_at": _ts(r.created_at),
        })
    return results


def _fetch_security(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT id::text, action, module, created_at
        FROM audit_log
        WHERE action = ANY(:actions)
          AND created_at >= NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC
        LIMIT 10
    """), {"actions": list(_SECURITY_ACTIONS)}).fetchall()
    return [
        {
            "id": f"security:{r.id}",
            "source": "SECURITY",
            "title": r.action.replace("_", " ").title(),
            "subtitle": r.module,
            "severity": "HIGH",
            "detected_at": _ts(r.created_at),
        }
        for r in rows
    ]


def _build_notifications() -> list[dict]:
    fetchers = [
        ("KAAVAL", _fetch_kaaval),
        ("KAVASAM", _fetch_kavasam),
        ("VERIFF", _fetch_veriff),
        ("SYSTEM", _fetch_system),
        ("ADMIN", _fetch_admin),
        ("SECURITY", _fetch_security),
    ]

    all_items: list[dict] = []
    with engine.connect() as conn:
        for source, fn in fetchers:
            try:
                all_items.extend(fn(conn))
            except Exception:
                # Skip sources whose tables don't exist or query fails
                pass

    # Deduplicate by (title + source + calendar date)
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for item in all_items:
        day = (item["detected_at"] or "")[:10]
        key = (item["title"], item["source"], day)
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    # Sort newest-first, null timestamps last
    deduped.sort(key=lambda x: x["detected_at"] or "", reverse=True)
    return deduped[:30]


# ── GET /api/notifications ────────────────────────────────
@router.get("")
def list_notifications():
    items = _build_notifications()
    notifications = [
        {**n, "read": n["id"] in _read_ids}
        for n in items
    ]
    unread_count = sum(1 for n in notifications if not n["read"])
    return {"notifications": notifications, "unread_count": unread_count}


# ── POST /api/notifications/read-all — before /{id} to avoid route conflict ─
@router.post("/read-all")
def mark_all_read():
    items = _build_notifications()
    ids = {n["id"] for n in items}
    _read_ids.update(ids)
    return {"marked": len(ids)}


# ── POST /api/notifications/{id}/read ────────────────────
@router.post("/{notification_id:path}/read")
def mark_read(notification_id: str):
    _read_ids.add(notification_id)
    return {"id": notification_id, "read": True}
