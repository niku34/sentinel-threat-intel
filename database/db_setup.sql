-- ============================================================
-- Sentinel — Full Database Setup
-- Tamil Broadcast AI Security Platform
--
-- Run as postgres superuser:
--   sudo -u postgres psql -f database/db_setup.sql
--
-- This file concatenates all migrations in order:
--   schema.sql → 004 → 006 → 007 → 008 → 009 → permissions.sql
-- ============================================================

-- ── Create database and application user ─────────────────
CREATE DATABASE sentinel;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinel_app') THEN
    CREATE USER sentinel_app WITH PASSWORD 'CHANGE_ME';
  END IF;
END$$;

GRANT ALL PRIVILEGES ON DATABASE sentinel TO sentinel_app;

-- ── Connect to the new database for all remaining DDL ─────
\connect sentinel

-- ============================================================
-- SECTION 1: Base Schema (schema.sql)
-- Core tables: users, channels, archive_clips, scans,
-- findings, alerts, plugins, api_keys, audit_log, cert_in_reports
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
     v_row::TEXT, v_ip::BYTEA, NOW());

  RETURN COALESCE(NEW, OLD);
END;
$$;

-- ── Macro: attach standard triggers to a table ────────────
CREATE OR REPLACE FUNCTION attach_standard_triggers(tbl TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format(
    'CREATE TRIGGER trg_%1$s_updated_at
       BEFORE UPDATE ON %1$I
       FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()',
    tbl
  );
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

-- ── 1. users ──────────────────────────────────────────────
CREATE TABLE users (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT        NOT NULL,
  email_hash    TEXT        NOT NULL UNIQUE,
  email         BYTEA       NOT NULL,
  role          TEXT        NOT NULL DEFAULT 'analyst'
                  CHECK (role IN ('admin', 'analyst', 'viewer')),
  password_hash TEXT        NOT NULL,
  mfa_secret    BYTEA,
  last_login    TIMESTAMPTZ,
  is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('users');
CREATE INDEX idx_users_role      ON users (role);
CREATE INDEX idx_users_is_active ON users (is_active);

-- ── 2. channels ───────────────────────────────────────────
CREATE TABLE channels (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT        NOT NULL,
  platform      TEXT        NOT NULL CHECK (platform IN ('youtube', 'twitch', 'rumble', 'other')),
  channel_id    TEXT        NOT NULL,
  status        TEXT        NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'paused', 'archived')),
  metadata_json BYTEA,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (platform, channel_id)
);

SELECT attach_standard_triggers('channels');
CREATE INDEX idx_channels_platform ON channels (platform);
CREATE INDEX idx_channels_status   ON channels (status);

-- ── 3. archive_clips ──────────────────────────────────────
CREATE TABLE archive_clips (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  title               TEXT        NOT NULL,
  keywords            TEXT[]      NOT NULL DEFAULT '{}',
  file_path           TEXT        NOT NULL,
  recorded_at         TIMESTAMPTZ NOT NULL,
  fingerprint_hash    TEXT        NOT NULL UNIQUE,
  duration_seconds    INTEGER     NOT NULL CHECK (duration_seconds > 0),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('archive_clips');
CREATE INDEX idx_archive_clips_recorded_at ON archive_clips (recorded_at DESC);
CREATE INDEX idx_archive_clips_keywords    ON archive_clips USING GIN (keywords);

-- ── 4. scans ──────────────────────────────────────────────
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

-- ── 5. findings ───────────────────────────────────────────
CREATE TABLE findings (
  id                    UUID  PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id               UUID  NOT NULL REFERENCES scans (id) ON DELETE CASCADE,
  severity              TEXT  NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
  module                TEXT  NOT NULL,
  title                 TEXT  NOT NULL,
  description           TEXT,
  evidence_json         BYTEA,
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

-- ── 6. alerts ─────────────────────────────────────────────
CREATE TABLE alerts (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  finding_id        UUID        NOT NULL REFERENCES findings (id) ON DELETE CASCADE,
  sent_to           BYTEA       NOT NULL,
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

-- ── 7. plugins ────────────────────────────────────────────
CREATE TABLE plugins (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT        NOT NULL UNIQUE,
  module      TEXT        NOT NULL UNIQUE,
  enabled     BOOLEAN     NOT NULL DEFAULT FALSE,
  config_json BYTEA,
  version     TEXT        NOT NULL DEFAULT '0.1.0',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT attach_standard_triggers('plugins');
CREATE INDEX idx_plugins_enabled ON plugins (enabled);

-- ── 8. api_keys ───────────────────────────────────────────
CREATE TABLE api_keys (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT        NOT NULL,
  key_hash        TEXT        NOT NULL UNIQUE,
  service         TEXT        NOT NULL,
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

-- ── 9. audit_log ──────────────────────────────────────────
CREATE TABLE audit_log (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID        REFERENCES users (id) ON DELETE SET NULL,
  action       TEXT        NOT NULL,
  module       TEXT        NOT NULL,
  details_json TEXT,
  ip_address   BYTEA,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_log_user_id    ON audit_log (user_id);
CREATE INDEX idx_audit_log_module     ON audit_log (module);
CREATE INDEX idx_audit_log_action     ON audit_log (action);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at DESC);

-- ── 10. cert_in_reports ───────────────────────────────────
CREATE TABLE cert_in_reports (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  finding_id       UUID        NOT NULL REFERENCES findings (id) ON DELETE RESTRICT,
  report_json      BYTEA       NOT NULL,
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

DROP FUNCTION attach_standard_triggers(TEXT);

ALTER TABLE users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE findings        ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log       ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys        ENABLE ROW LEVEL SECURITY;
ALTER TABLE cert_in_reports ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- SECTION 2: Migration 004 — Finding Playbooks
-- ============================================================

CREATE TABLE IF NOT EXISTS finding_playbooks (
  id SERIAL PRIMARY KEY,
  threat_type VARCHAR NOT NULL UNIQUE,
  what_it_means TEXT NOT NULL,
  remediation_steps JSONB NOT NULL,
  who_to_contact VARCHAR NOT NULL,
  legal_implications TEXT NOT NULL,
  resolution_criteria TEXT NOT NULL
);

INSERT INTO finding_playbooks
  (threat_type, what_it_means, remediation_steps, who_to_contact, legal_implications, resolution_criteria)
VALUES
  ('DEEPFAKE_AUDIO',
   'AI-cloned anchor voice circulating on social media',
   '["File Telegram/WhatsApp takedown via India safe-harbor","Issue on-air clarification within 2 hours","Add voice fingerprint to Kaaval probe library","Log incident in audit trail"]',
   'Editorial Head + IT Admin',
   'Defamation exposure + IT Act 2000 S.66D',
   'Takedown confirmed + fingerprint added'),

  ('BRIGADE_ATTACK',
   'Coordinated accounts mass-commenting to manipulate YouTube algorithm',
   '["Bulk-flag comments via YouTube Studio","Report channel to YouTube Trust & Safety","Add account pattern to Kaaval brigade detector","Monitor for re-emergence 48h"]',
   'IT Admin',
   'IT Act 2000 S.66 — harassment',
   'Comments removed + accounts flagged'),

  ('FAKE_PAGE',
   'Impersonator page using Example Broadcaster branding to mislead audience',
   '["File platform IP/impersonation report","Document evidence (screenshots + URLs)","Issue public warning on official channels","Add page pattern to Kaaval monitor"]',
   'Editorial Head + Legal',
   'IT Act S.66C + Trademark Act 1999',
   'Page taken down or labelled fake'),

  ('FOOTAGE_THEFT',
   'Example Broadcaster archival footage reused without permission',
   '["File DMCA takedown draft (auto-generated)","Document original broadcast timestamp","Contact platform rights team","Log in DPDP audit trail"]',
   'Legal + IT Admin',
   'Copyright Act 1957 + IT Act',
   'Content removed or credited'),

  ('CERT_IN_INCIDENT',
   'Security breach requiring mandatory 6-hour CERT-In report',
   '["File auto-drafted CERT-In report immediately","Preserve all logs tamper-proof","Notify internal stakeholders","Conduct post-incident review"]',
   'IT Admin + CEO',
   'CERT-In Directions 2022 — up to Rs.1 Crore penalty if missed',
   'Report filed within 6 hours + logs preserved 180 days'),

  ('RECYCLED_FOOTAGE',
   'Old Example Broadcaster or third-party footage recirculated with false context',
   '["Verify original broadcast date via archive fingerprint","Issue correction on Example News newsroom","Add clip hash to Veriff probe library","Inform journalist who submitted for verification"]',
   'Editorial Head',
   'Defamation risk if broadcast without verification',
   'Correction issued + clip fingerprinted'),

  ('LOGO_MISUSE',
   'Example Broadcaster logo used without authorization on external content',
   '["Document unauthorized usage with screenshots","File platform IP complaint","Send cease-and-desist via legal","Add logo variant to detection model"]',
   'Legal',
   'Trademark Act 1999 + Copyright Act 1957',
   'Logo removed or licensing agreed')

ON CONFLICT (threat_type) DO NOTHING;

GRANT SELECT, INSERT, UPDATE, DELETE ON finding_playbooks TO sentinel_app;
GRANT USAGE, SELECT ON SEQUENCE finding_playbooks_id_seq TO sentinel_app;

-- ============================================================
-- SECTION 3: Migration 006 — Plugins Fix Constraints + Seeds
-- ============================================================

ALTER TABLE plugins DROP CONSTRAINT IF EXISTS plugins_module_key;

INSERT INTO plugins (id, name, module, enabled, version, created_at, updated_at) VALUES
  -- KAAVAL
  (gen_random_uuid(), 'Video Forensics',        'kaaval',  true,  'v2.1', NOW(), NOW()),
  (gen_random_uuid(), 'Social Listening',       'kaaval',  true,  'v1.3', NOW(), NOW()),
  (gen_random_uuid(), 'Tamil NLP',              'kaaval',  true,  'v3.0', NOW(), NOW()),
  (gen_random_uuid(), 'Fact-Check Cross-Ref',   'kaaval',  true,  'v1.1', NOW(), NOW()),
  (gen_random_uuid(), 'Editorial Brief',        'kaaval',  true,  'v1.0', NOW(), NOW()),
  (gen_random_uuid(), 'Threads Listener',       'kaaval',  false, 'v0.3', NOW(), NOW()),
  -- KAVASAM
  (gen_random_uuid(), 'CERT-In Drafter',        'kavasam', true,  'v1.4', NOW(), NOW()),
  (gen_random_uuid(), 'DPDP Audit Logger',      'kavasam', true,  'v2.0', NOW(), NOW()),
  (gen_random_uuid(), 'Channel Health Monitor', 'kavasam', true,  'v1.2', NOW(), NOW()),
  (gen_random_uuid(), 'Alert Router',           'kavasam', true,  'v1.0', NOW(), NOW()),
  -- VERIFF
  (gen_random_uuid(), 'InVID Connector',        'veriff',  true,  'v1.2', NOW(), NOW()),
  (gen_random_uuid(), 'BOOM Fact-Check',        'veriff',  true,  'v1.0', NOW(), NOW()),
  (gen_random_uuid(), 'AltNews Scraper',        'veriff',  true,  'v1.1', NOW(), NOW()),
  (gen_random_uuid(), 'Vishvas News',           'veriff',  true,  'v1.0', NOW(), NOW()),
  (gen_random_uuid(), 'Fact Crescendo Tamil',   'veriff',  false, 'v0.9', NOW(), NOW()),
  (gen_random_uuid(), 'Newschecker Tamil',      'veriff',  true,  'v1.0', NOW(), NOW())
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- SECTION 4: Migration 007 — Anchor Voice Profiles
-- ============================================================

CREATE TABLE IF NOT EXISTS anchor_voice_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  anchor_name VARCHAR NOT NULL,
  channel VARCHAR NOT NULL,
  fingerprint_data TEXT NOT NULL,
  sample_duration_seconds FLOAT,
  audio_source VARCHAR,
  file_type VARCHAR DEFAULT 'audio',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE anchor_voice_profiles TO sentinel_app;

-- ============================================================
-- SECTION 5: Migration 008 — Meta Rights Manager
-- ============================================================

CREATE TABLE IF NOT EXISTS reference_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR NOT NULL,
  channel VARCHAR NOT NULL,
  file_path VARCHAR,
  broadcast_date DATE,
  submitted_to_meta BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meta_strikes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  platform VARCHAR NOT NULL,
  content_url TEXT NOT NULL,
  reference_file_id UUID REFERENCES reference_files(id),
  notes TEXT,
  status VARCHAR DEFAULT 'DRAFT',
  filed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE reference_files TO sentinel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE meta_strikes    TO sentinel_app;

-- ============================================================
-- SECTION 6: Migration 009 — Claim Embeddings (pgvector)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS claim_embeddings (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_text   TEXT NOT NULL,
    claim_hash   VARCHAR(64) UNIQUE NOT NULL,
    embedding    vector(384),
    verdict      VARCHAR,
    confidence   FLOAT,
    sources_json TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_used    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS claim_embeddings_vector_idx
    ON claim_embeddings USING ivfflat (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS claim_embeddings_last_used_idx
    ON claim_embeddings (last_used);

GRANT ALL PRIVILEGES ON claim_embeddings TO sentinel_app;

-- ============================================================
-- SECTION 7: Permissions (permissions.sql)
-- Users, roles, RLS policies
-- ============================================================

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinel_app') THEN
    CREATE ROLE sentinel_app LOGIN PASSWORD 'CHANGE_ME';
  END IF;
END$$;

GRANT CONNECT ON DATABASE sentinel TO sentinel_app;
GRANT USAGE   ON SCHEMA public        TO sentinel_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  users, channels, archive_clips, scans, findings,
  alerts, plugins, api_keys, cert_in_reports
TO sentinel_app;

GRANT SELECT, INSERT ON TABLE audit_log TO sentinel_app;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO sentinel_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sentinel_app;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sentinel_readonly') THEN
    CREATE ROLE sentinel_readonly LOGIN PASSWORD 'CHANGE_ME_readonly';
  END IF;
END$$;

GRANT CONNECT ON DATABASE sentinel    TO sentinel_readonly;
GRANT USAGE   ON SCHEMA public          TO sentinel_readonly;
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO sentinel_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO sentinel_readonly;

ALTER ROLE sentinel_app SET row_security = off;

CREATE POLICY audit_log_readonly_read ON audit_log
  FOR SELECT TO sentinel_readonly USING (TRUE);

CREATE POLICY api_keys_readonly_read ON api_keys
  FOR SELECT TO sentinel_readonly USING (TRUE);

CREATE POLICY findings_readonly_read ON findings
  FOR SELECT TO sentinel_readonly USING (TRUE);

CREATE POLICY users_readonly_read ON users
  FOR SELECT TO sentinel_readonly USING (TRUE);

CREATE POLICY cert_in_reports_readonly_read ON cert_in_reports
  FOR SELECT TO sentinel_readonly USING (TRUE);
