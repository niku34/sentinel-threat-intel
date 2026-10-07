-- ============================================================
-- Broadcaster Shield — Database Users & Permissions
-- Run as superuser (postgres) on the sentinel database
-- ============================================================

-- ── sentinel_app ───────────────────────────────────────
-- Application user: DML on data tables, append-only on audit_log,
-- no DROP / TRUNCATE / schema changes.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinel_app') THEN
    CREATE ROLE sentinel_app LOGIN PASSWORD 'CHANGE_ME_app';
  END IF;
END$$;

GRANT CONNECT ON DATABASE sentinel TO sentinel_app;
GRANT USAGE   ON SCHEMA public        TO sentinel_app;

-- Full DML on operational tables
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  users, channels, archive_clips, scans, findings,
  alerts, plugins, api_keys, cert_in_reports
TO sentinel_app;

-- audit_log is append-only for the app
GRANT SELECT, INSERT ON TABLE audit_log TO sentinel_app;

-- Allow the app to call pgcrypto functions
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO sentinel_app;

-- Future tables inherit the same grants automatically
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sentinel_app;

-- ── sentinel_readonly ──────────────────────────────────
-- Read-only reporting / debugging user: SELECT only, no writes.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinel_readonly') THEN
    CREATE ROLE sentinel_readonly LOGIN PASSWORD 'CHANGE_ME_readonly';
  END IF;
END$$;

GRANT CONNECT ON DATABASE sentinel    TO sentinel_readonly;
GRANT USAGE   ON SCHEMA public          TO sentinel_readonly;
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO sentinel_readonly;

-- Future tables automatically readable
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO sentinel_readonly;

-- ── RLS policies ─────────────────────────────────────────
-- sentinel_app bypasses RLS so it can serve all roles.
-- sentinel_readonly gets a permissive read-all policy.

ALTER ROLE sentinel_app SET row_security = off;

-- Audit log: readonly can read all rows
CREATE POLICY audit_log_readonly_read ON audit_log
  FOR SELECT TO sentinel_readonly USING (TRUE);

-- API keys: readonly can see metadata but NOT key_hash
CREATE POLICY api_keys_readonly_read ON api_keys
  FOR SELECT TO sentinel_readonly USING (TRUE);

-- Findings: readonly sees all findings
CREATE POLICY findings_readonly_read ON findings
  FOR SELECT TO sentinel_readonly USING (TRUE);

-- Users: readonly sees all user rows (emails remain encrypted)
CREATE POLICY users_readonly_read ON users
  FOR SELECT TO sentinel_readonly USING (TRUE);

-- cert_in_reports: readonly sees all
CREATE POLICY cert_in_reports_readonly_read ON cert_in_reports
  FOR SELECT TO sentinel_readonly USING (TRUE);
