-- 014_findings_app_rls_policy.sql
-- Digital Kavasam mass-deletion / anomaly detection writes real findings.
--
-- `findings` already has table-level GRANTs for sentinel_app
-- (INSERT/SELECT/UPDATE/DELETE — confirmed via information_schema), but Row
-- Level Security is enabled on the table with only one policy:
--
--   findings_readonly_read  PERMISSIVE  SELECT  role=sentinel_readonly  qual=true
--
-- With RLS enabled and no policy naming sentinel_app, Postgres denies that
-- role ALL access regardless of table grants (RLS policies are additive on
-- top of GRANTs, not a replacement — the app role's real GRANTs are useless
-- without a matching policy). Confirmed empirically: a plain SELECT as
-- sentinel_app fails with "InsufficientPrivilege ... row-level security
-- policy for table findings", not a GRANT error.
--
-- This is the same shape of gap as the earlier scans-table ownership issue —
-- application code has been ready to write here, but the DB has never
-- actually allowed it. Kavasam's new mass-deletion detector degrades
-- gracefully (logs a warning, does not crash the scan) until this is applied,
-- but findings will not actually persist until it is.
--
-- Run as superuser (postgres) on the sentinel database:
--   sudo -u postgres psql -d sentinel -f database/migrations/014_findings_app_rls_policy.sql

CREATE POLICY findings_app_all
    ON findings
    FOR ALL
    TO sentinel_app
    USING (true)
    WITH CHECK (true);
