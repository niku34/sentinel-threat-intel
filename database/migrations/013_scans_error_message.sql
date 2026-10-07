-- ============================================================
-- 013 — scans.error_message
-- ============================================================
--
-- WHAT WAS WRONG
--
-- api/routers/scan.py's background scan runner caught every exception from
-- the agent subprocesses (including subprocess.TimeoutExpired) with a bare
-- `except Exception: pass`, then unconditionally set status = 'completed'
-- with whatever findings_count had accumulated (often 0). A scan that timed
-- out or crashed was indistinguishable from one that genuinely found
-- nothing — there was nowhere to record what actually happened.
--
-- This adds a nullable error_message column so a failed scan can record why,
-- for the API/frontend to surface honestly instead of rendering a plausible
-- "completed, 0 findings" result.

ALTER TABLE scans ADD COLUMN IF NOT EXISTS error_message TEXT;
