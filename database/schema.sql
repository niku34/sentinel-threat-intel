-- ============================================================
-- Broadcaster Shield — PostgreSQL Schema
-- PostgreSQL 16+  |  pgcrypto required
-- ============================================================
-- Encryption strategy:
--   Sensitive fields stored as BYTEA encrypted with pgp_sym_encrypt().
--   The app sets: SET LOCAL app.encryption_key = '<key>';
--   For unique-lookup columns (e.g. email) a separate deterministic
--   HMAC-SHA256 hash column is stored alongside the ciphertext so
--   equality queries work without decrypting every row.
-- ============================================================

-- ── Extensions ───────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Helper: auto-update updated_at ───────────────────────
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

-- ── Helper: write audit record ────────────────────────────
-- Caller MUST set session-local vars before DML:
--   SET LOCAL app.current_user_id = '<uuid>';
--   SET LOCAL app.client_ip       = '<ip>';
CREATE OR REPLACE FUNCTION fn_audit_log()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
  v_user_id UUID;
  v_ip      TEXT;
  v_row     JSONB;
BEGIN
  v_user_id := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
  v_ip      := NULLIF(current_setting('app.client_ip',       TRUE), '');

  v_row := CASE TG_OP
             WHEN 'DELETE' THEN to_jsonb(OLD)
             ELSE               to_jsonb(NEW)
           END;

  INSERT INTO audit_log
    (id, user_id, action, module, details_json, ip_address, created_at)
  VALUES
    (gen_random_uuid(), v_user_id, TG_OP, TG_TABLE_NAME,
     v_row::TEXT, v_ip, NOW());

  RETURN COALESCE(NEW, OLD);
END;
$$;

-- ── Macro: attach standard triggers to a table ────────────
-- Called once per table after CREATE TABLE.
-- Usage: SELECT attach_standard_triggers('table_name');
CREATE OR REPLACE FUNCTION attach_standard_triggers(tbl TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format(
    'CREATE TRIGGER trg_%1$s_updated_at
       BEFORE UPDATE ON %1$I
       FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()',
    tbl
  );
  -- audit trigger — skip audit_log itself to prevent recursion
  IF tbl <> 'audit_log' THEN
    EXECUTE format(
      'CREATE TRIGGER trg_%1$s_audit
         AFTER INSERT OR UPDATE OR DELETE ON %1$I
         FOR EACH ROW EXECUTE FUNCTION fn_audit_log()',
      tbl
    );
  END IF;
END;
$$;

-- ============================================================
-- 1. users
-- ============================================================
CREATE TABLE users (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT        NOT NULL,
  -- email stored encrypted; email_hash enables unique lookup
  email_hash    TEXT        NOT NULL UNIQUE,   -- hmac(email, key, 'sha256') hex
  email         BYTEA       NOT NULL,          -- pgp_sym_encrypt(email, key)
  role          TEXT        NOT NULL DEFAULT 'analyst'
                  CHECK (role IN ('admin', 'analyst', 'viewer')),
  password_hash TEXT        NOT NULL,          -- bcrypt via pgcrypto crypt()
  mfa_secret    BYTEA,                         -- pgp_sym_encrypt(totp_secret, key)
  last_login    TIMESTAMPTZ,
  is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('users');
CREATE INDEX idx_users_role      ON users (role);
CREATE INDEX idx_users_is_active ON users (is_active);

-- ============================================================
-- 2. channels
-- ============================================================
CREATE TABLE channels (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT        NOT NULL,
  platform      TEXT        NOT NULL CHECK (platform IN ('youtube', 'twitch', 'rumble', 'other')),
  channel_id    TEXT        NOT NULL,
  status        TEXT        NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'paused', 'archived')),
  metadata_json BYTEA,                         -- pgp_sym_encrypt(json, key)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (platform, channel_id)
);

SELECT attach_standard_triggers('channels');
CREATE INDEX idx_channels_platform ON channels (platform);
CREATE INDEX idx_channels_status   ON channels (status);

-- ============================================================
-- 3. archive_clips
-- ============================================================
CREATE TABLE archive_clips (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  title               TEXT        NOT NULL,
  keywords            TEXT[]      NOT NULL DEFAULT '{}',
  file_path           TEXT        NOT NULL,
  recorded_at         TIMESTAMPTZ NOT NULL,
  fingerprint_hash    TEXT        NOT NULL UNIQUE, -- SHA-256 of raw media
  duration_seconds    INTEGER     NOT NULL CHECK (duration_seconds > 0),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('archive_clips');
CREATE INDEX idx_archive_clips_recorded_at ON archive_clips (recorded_at DESC);
CREATE INDEX idx_archive_clips_keywords    ON archive_clips USING GIN (keywords);

-- ============================================================
-- 4. scans
-- ============================================================
CREATE TABLE scans (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  channel_id      UUID        NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
  scan_type       TEXT        NOT NULL CHECK (scan_type IN ('full', 'incremental', 'targeted')),
  status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  findings_count  INTEGER     NOT NULL DEFAULT 0 CHECK (findings_count >= 0),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_scan_timeline CHECK (
    completed_at IS NULL OR completed_at >= started_at
  )
);

SELECT attach_standard_triggers('scans');
CREATE INDEX idx_scans_channel_id ON scans (channel_id);
CREATE INDEX idx_scans_status     ON scans (status);
CREATE INDEX idx_scans_started_at ON scans (started_at DESC);

-- ============================================================
-- 5. findings
-- ============================================================
CREATE TABLE findings (
  id                    UUID  PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id               UUID  NOT NULL REFERENCES scans (id) ON DELETE CASCADE,
  severity              TEXT  NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
  module                TEXT  NOT NULL,
  title                 TEXT  NOT NULL,
  description           TEXT,
  evidence_json         BYTEA,                   -- pgp_sym_encrypt(json, key)
  matched_archive_clip_id UUID REFERENCES archive_clips (id) ON DELETE SET NULL,
  status                TEXT  NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'reviewing', 'confirmed', 'dismissed', 'resolved')),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('findings');
CREATE INDEX idx_findings_scan_id   ON findings (scan_id);
CREATE INDEX idx_findings_severity  ON findings (severity);
CREATE INDEX idx_findings_status    ON findings (status);
CREATE INDEX idx_findings_module    ON findings (module);

-- ============================================================
-- 6. alerts
-- ============================================================
CREATE TABLE alerts (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  finding_id        UUID        NOT NULL REFERENCES findings (id) ON DELETE CASCADE,
  sent_to           BYTEA       NOT NULL,         -- pgp_sym_encrypt(recipient, key)
  sent_via          TEXT        NOT NULL CHECK (sent_via IN ('email', 'slack', 'webhook', 'sms')),
  sent_at           TIMESTAMPTZ,
  acknowledged_at   TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_alert_timeline CHECK (
    acknowledged_at IS NULL OR (sent_at IS NOT NULL AND acknowledged_at >= sent_at)
  )
);

SELECT attach_standard_triggers('alerts');
CREATE INDEX idx_alerts_finding_id ON alerts (finding_id);
CREATE INDEX idx_alerts_sent_at    ON alerts (sent_at DESC);

-- ============================================================
-- 7. plugins
-- ============================================================
CREATE TABLE plugins (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT        NOT NULL UNIQUE,
  module      TEXT        NOT NULL UNIQUE,        -- python import path
  enabled     BOOLEAN     NOT NULL DEFAULT FALSE,
  config_json BYTEA,                              -- pgp_sym_encrypt(json, key)
  version     TEXT        NOT NULL DEFAULT '0.1.0',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('plugins');
CREATE INDEX idx_plugins_enabled ON plugins (enabled);

-- ============================================================
-- 8. api_keys
-- ============================================================
CREATE TABLE api_keys (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT        NOT NULL,
  key_hash        TEXT        NOT NULL UNIQUE,    -- SHA-256 of raw key (lookup only)
  service         TEXT        NOT NULL,           -- e.g. 'youtube', 'slack'
  last_rotated_at TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_api_key_expiry CHECK (
    expires_at IS NULL OR expires_at > created_at
  )
);

SELECT attach_standard_triggers('api_keys');
CREATE INDEX idx_api_keys_service    ON api_keys (service);
CREATE INDEX idx_api_keys_expires_at ON api_keys (expires_at);

-- ============================================================
-- 9. audit_log   (no updated_at — records are immutable)
-- ============================================================
CREATE TABLE audit_log (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID        REFERENCES users (id) ON DELETE SET NULL,
  action       TEXT        NOT NULL,              -- INSERT / UPDATE / DELETE / LOGIN / etc.
  module       TEXT        NOT NULL,              -- table name or feature area
  details_json TEXT,                              -- row snapshot or free-form JSON
  ip_address   BYTEA,                             -- pgp_sym_encrypt(ip, key)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Audit log is append-only: no UPDATE trigger, no audit-on-audit trigger
CREATE INDEX idx_audit_log_user_id    ON audit_log (user_id);
CREATE INDEX idx_audit_log_module     ON audit_log (module);
CREATE INDEX idx_audit_log_action     ON audit_log (action);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at DESC);

-- ============================================================
-- 10. cert_in_reports
-- ============================================================
CREATE TABLE cert_in_reports (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  finding_id       UUID        NOT NULL REFERENCES findings (id) ON DELETE RESTRICT,
  report_json      BYTEA       NOT NULL,          -- pgp_sym_encrypt(json, key)
  status           TEXT        NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'filed', 'acknowledged', 'closed')),
  filed_at         TIMESTAMPTZ,
  reference_number TEXT        UNIQUE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('cert_in_reports');
CREATE INDEX idx_cert_in_reports_finding_id ON cert_in_reports (finding_id);
CREATE INDEX idx_cert_in_reports_status     ON cert_in_reports (status);

-- ============================================================
-- Clean up the bootstrap helper (no longer needed at runtime)
-- ============================================================
DROP FUNCTION attach_standard_triggers(TEXT);

-- ============================================================
-- Row-level security: enable on sensitive tables
-- (policies added per-role after users are created)
-- ============================================================
ALTER TABLE users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE findings        ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log       ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys        ENABLE ROW LEVEL SECURITY;
ALTER TABLE cert_in_reports ENABLE ROW LEVEL SECURITY;
