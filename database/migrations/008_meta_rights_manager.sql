-- ============================================================
-- 008 — Meta integration: Rights Manager reference files + strike tracker
-- Run as superuser (postgres) on the sentinel database:
--   sudo -u postgres psql -d sentinel -f database/migrations/008_meta_rights_manager.sql
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

-- Application user needs DML (schema owner is postgres).
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE reference_files TO sentinel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE meta_strikes    TO sentinel_app;
