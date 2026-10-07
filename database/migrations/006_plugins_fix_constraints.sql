-- Drop the single-row-per-module constraints so we can have
-- multiple plugins per module (e.g. 6 in kaaval, 4 in kavasam, 6 in veriff).
-- UNIQUE(name) is kept — plugin names must still be globally unique.
ALTER TABLE plugins DROP CONSTRAINT IF EXISTS plugins_module_key;

-- Seed initial plugin catalogue
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
