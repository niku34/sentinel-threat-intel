-- Run as postgres superuser:
--   psql -U postgres sentinel -f database/migrations/004_finding_playbooks.sql

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

-- Grant access to the app user
GRANT SELECT, INSERT, UPDATE, DELETE ON finding_playbooks TO sentinel_app;
GRANT USAGE, SELECT ON SEQUENCE finding_playbooks_id_seq TO sentinel_app;
