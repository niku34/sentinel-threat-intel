-- ============================================================
-- 010 — WORM audit log: append-only trigger + hash chain columns
-- Run as superuser (postgres) on the sentinel database:
--   sudo -u postgres psql -d sentinel -f database/migrations/010_worm_audit_log.sql
-- ============================================================

-- Hash chain columns for tamper-evident chain of custody
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS entry_hash    VARCHAR(64);
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS previous_hash VARCHAR(64);

-- WORM trigger: block all UPDATE and DELETE on audit_log
CREATE OR REPLACE FUNCTION audit_log_worm()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only (WORM). Updates and deletes are not permitted.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_worm_trigger
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_worm();

-- Grant the app user permission to call the function
GRANT EXECUTE ON FUNCTION audit_log_worm() TO sentinel_app;
