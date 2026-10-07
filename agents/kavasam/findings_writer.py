"""Write a Kavasam anomaly into the real `findings` table.

Best-effort and isolated: `findings` has Row Level Security enabled and — at
the time this was written — no policy exists granting the `sentinel_app`
role access (only a SELECT-only policy for `sentinel_readonly`), so this
degrades to a logged warning until
database/migrations/014_findings_app_rls_policy.sql is applied by a
superuser. See that migration for the exact grant needed and why.

Runs in its own short-lived session/transaction, never the caller's — a
`findings` insert failure (RLS denial, FK violation) must never roll back
the caller's already-staged ChannelSnapshot/CERT-In-draft work for this scan.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("kavasam.findings_writer")

# Matches the findings_severity_check constraint (lowercase only).
_VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}


def insert_finding(
    engine,
    scan_id: str,
    severity: str,
    module: str,
    title: str,
    description: str,
) -> str | None:
    """Insert one row into `findings`. Returns the new id (str), or None.

    `scan_id` must be a real `scans.id` — the column is NOT NULL with an
    ON DELETE CASCADE FK, so this is skipped (not attempted) when no scan_id
    is available, e.g. a standalone/CLI run outside the "+ New Scan" flow.
    """
    if not scan_id:
        logger.info(
            "insert_finding: no scan_id available (standalone run?) — skipping "
            "findings-table write for %r (channel_snapshots record still holds "
            "the anomaly)",
            title,
        )
        return None

    sev = (severity or "").lower()
    if sev not in _VALID_SEVERITIES:
        logger.warning("insert_finding: invalid severity %r — skipping", severity)
        return None

    try:
        with Session(engine) as session:
            # `sentinel_app` has a stale `ALTER ROLE ... SET row_security =
            # off` from an earlier (wrong) attempt to work around missing RLS
            # policies — with row_security off and this role neither owning
            # `findings` nor holding BYPASSRLS, Postgres refuses ANY query
            # against an RLS-enabled table outright (not "unfiltered", an
            # actual error) rather than silently doing something unsafe.
            # `findings_app_all` (014_findings_app_rls_policy.sql) is correct
            # and sufficient once row_security is back on for this session —
            # confirmed empirically. Overriding it here means this doesn't
            # depend on a further ALTER ROLE from a superuser, but that stale
            # setting is still worth fixing at the role level since it would
            # silently break any *other* future RLS-table access for this
            # role the same way (e.g. cert_in_reports, which also has RLS).
            session.execute(text("SET row_security = on"))
            row = session.execute(
                text(
                    "INSERT INTO findings "
                    "(scan_id, severity, module, title, description, status) "
                    "VALUES (:scan_id, :severity, :module, :title, :description, 'open') "
                    "RETURNING id"
                ),
                {
                    "scan_id": scan_id,
                    "severity": sev,
                    "module": module,
                    "title": title,
                    "description": description,
                },
            ).first()
            session.commit()
            finding_id = str(row[0]) if row else None
            if finding_id:
                logger.warning("Finding %s created: %s", finding_id, title)
            return finding_id
    except Exception as exc:  # noqa: BLE001 — RLS/permission/FK failure must not crash the scan
        logger.warning(
            "findings insert failed for %r (scan_id=%s, module=%s): %s — "
            "is database/migrations/014_findings_app_rls_policy.sql applied?",
            title, scan_id, module, exc,
        )
        return None
