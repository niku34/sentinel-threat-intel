-- ============================================================
-- 007 — Anchor voice fingerprint profiles
-- Run as superuser (postgres) on the sentinel database:
--   sudo -u postgres psql -d sentinel -f database/migrations/007_anchor_voice_profiles.sql
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

-- Application user needs DML (schema owner is postgres).
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE anchor_voice_profiles TO sentinel_app;
