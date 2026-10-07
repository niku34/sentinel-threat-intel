-- ============================================================
-- 012 — Audit trigger hash chain (fixes coverage gap left by 010)
-- Run as superuser (postgres) on the sentinel database:
--   sudo -u postgres psql -d sentinel -f database/migrations/012_audit_trigger_hash_chain.sql
-- ============================================================
--
-- WHAT WAS WRONG
--
-- Migration 010 added the entry_hash / previous_hash columns and the WORM
-- trigger, and the application writers in routers/auth.py were taught to fill
-- them. But fn_audit_log() — the table-level trigger that writes the majority
-- of audit rows (INSERT/UPDATE/DELETE on channels, archive_clips, scans,
-- findings, alerts, plugins, users) — was never updated. It kept inserting
-- rows with both hash columns NULL.
--
-- Result: coverage never improved. At the time of this migration the audit log
-- held 66 entries of which exactly 1 was chained, and 25 of the 65 unchained
-- entries were written AFTER 010 was applied. /api/audit/metrics honestly
-- reported PARTIAL, but the underlying cause was a bug, not a design limit.
--
-- WHAT THIS DOES
--
-- 1. fn_audit_log() now computes previous_hash and entry_hash using the same
--    canonical payload as the Python side (api/core/audit_hash.py):
--        previous_hash | canonical_ts | who | action | details_json
--    The SQL expression below was verified to reproduce an existing
--    Python-generated hash byte-for-byte before this migration was written.
--
-- 2. NOW() is replaced with clock_timestamp(). NOW() returns the transaction
--    start time, so every row touched by a multi-row statement received an
--    IDENTICAL created_at — nine archive_clips deletes all landed on
--    2026-08-02 13:29:36.583404+00. The verifier walks the chain ordered by
--    created_at, so tied timestamps make the walk order ambiguous and would
--    have produced spurious tampering reports. clock_timestamp() advances per
--    row, giving the chain a deterministic order.
--
-- WHAT THIS DELIBERATELY DOES NOT DO
--
-- It does NOT backfill hashes onto the 65 pre-existing unchained rows.
-- Retro-hashing them would manufacture a chain of custody for entries whose
-- integrity was never actually protected — the tamper-evidence would be
-- decorative. Those rows stay NULL and are reported as outside the chain.
-- Coverage climbs from here as new entries are written.
-- ============================================================

CREATE OR REPLACE FUNCTION public.fn_audit_log()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $function$
DECLARE
  v_user_id  UUID;
  v_ip       TEXT;
  v_row      JSONB;
  v_details  TEXT;
  v_now      TIMESTAMPTZ;
  v_prev     TEXT;
  v_actor    TEXT;
  v_hash     TEXT;
BEGIN
  v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
  v_ip      := NULLIF(current_setting('app.client_ip',       TRUE), '');

  v_row := CASE TG_OP
             WHEN 'DELETE' THEN to_jsonb(OLD)
             ELSE               to_jsonb(NEW)
           END;

  -- Hash the exact text that gets stored, so the verifier recomputes from
  -- identical input.
  v_details := v_row::TEXT;

  -- Per-row timestamp: NOW() would tie every row in a multi-row statement.
  v_now := clock_timestamp();

  -- Tail of the existing chain. Rows inserted earlier in this same
  -- transaction are visible here, so sequential row triggers chain correctly.
  SELECT entry_hash INTO v_prev
    FROM audit_log
   WHERE entry_hash IS NOT NULL
   ORDER BY created_at DESC
   LIMIT 1;
  v_prev := COALESCE(v_prev, 'GENESIS');

  -- Same precedence as actor_from_details() in api/core/audit_hash.py.
  v_actor := COALESCE(v_row->>'user', v_row->>'username', v_row->>'email', '');

  v_hash := encode(
    digest(
      v_prev || '|' ||
      to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00' || '|' ||
      v_actor || '|' ||
      TG_OP   || '|' ||
      COALESCE(v_details, ''),
      'sha256'
    ),
    'hex'
  );

  INSERT INTO audit_log
    (id, user_id, action, module, details_json, ip_address, created_at,
     entry_hash, previous_hash)
  VALUES
    (gen_random_uuid(), v_user_id, TG_OP, TG_TABLE_NAME,
     v_details, v_ip, v_now, v_hash, v_prev);

  RETURN COALESCE(NEW, OLD);
END;
$function$;

-- digest() comes from pgcrypto; confirm it is present (it is, but fail loudly
-- rather than silently writing NULL hashes if this ever runs elsewhere).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
    RAISE EXCEPTION 'pgcrypto is required for the audit hash chain — CREATE EXTENSION pgcrypto;';
  END IF;
END $$;
