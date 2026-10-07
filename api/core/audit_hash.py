"""Canonical audit-log hash-chain format.

Three writers must agree byte-for-byte or the chain will not verify:

  1. ``routers/auth.py``            — login / anomaly events (Python)
  2. ``fn_audit_log()`` DB trigger  — table INSERT/UPDATE/DELETE events (SQL)
  3. ``routers/audit.py``           — the verifier that recomputes the chain

The hashed payload is::

    previous_hash | canonical_ts | who | action | details_json

``canonical_ts`` is deliberately NOT ``datetime.isoformat()``. isoformat omits
the microsecond component entirely when it happens to be exactly zero, which
would make one entry in a million hash differently on write and on verify and
surface as a false TAMPERING DETECTED. This format always emits 6 microsecond
digits and an explicit +00:00 offset, matching the SQL side:

    to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00'

``who`` is extracted from details_json with the same precedence in both
languages: user, then username, then email, then empty string.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

GENESIS = "GENESIS"


def canonical_ts(dt: datetime) -> str:
    """Timestamp string used inside the hash. Always UTC, always 6 µs digits."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+00:00"


def actor_from_details(details_json: str | None) -> str:
    """Extract the acting user from details_json — same precedence as the SQL side."""
    if not details_json:
        return ""
    try:
        d = json.loads(details_json)
    except Exception:  # noqa: BLE001 — a row dump that is not an object
        return ""
    if not isinstance(d, dict):
        return ""
    return d.get("user") or d.get("username") or d.get("email") or ""


def audit_entry_hash(
    previous_hash: str,
    created_at: datetime,
    actor: str,
    action: str,
    details_json: str | None,
) -> str:
    """SHA-256 over the canonical payload."""
    payload = (
        f"{previous_hash}|{canonical_ts(created_at)}|{actor}|{action}|{details_json or ''}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()
