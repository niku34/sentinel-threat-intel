-- Run as postgres superuser after 004_finding_playbooks.sql:
--   psql -U postgres sentinel -f database/migrations/011_playbooks_kavasam_kaaval.sql

INSERT INTO finding_playbooks
  (threat_type, what_it_means, remediation_steps, who_to_contact, legal_implications, resolution_criteria)
VALUES

  ('CHANNEL_STATUS_CHANGE',
   'YouTube channel visibility or status changed without authorization — possible account compromise or platform action',
   '["Immediately verify change in YouTube Studio → Settings → Channel Status","Check admin access log for unauthorized logins","If unauthorized: rotate all YouTube account credentials within 30 minutes","Enable 2-step verification if not already active","If data breach suspected: file CERT-In report within 6 hours","Document all changes with timestamps for DPDP audit trail"]',
   'IT Admin (immediate) + Editorial Head',
   'IT Act 2000 S.43A — data security obligation. CERT-In Directions 2022 if credentials compromised',
   'Channel status restored + credentials rotated + 2FA enabled + audit documented'),

  ('SUBSCRIBER_DROP',
   'Sudden subscriber loss detected — possible coordinated unsubscribe attack, YouTube bot purge, or content policy action',
   '["Check YouTube Studio Analytics for drop timeline","Cross-reference with YouTube''s known bot purge dates","If organic purge: monitor for 48 hours, no action needed","If coordinated attack: document accounts involved and report to YouTube Trust & Safety","If content policy action: review flagged content in YouTube Studio","Post transparent communication on official Example Broadcaster social if loss exceeds 5%"]',
   'IT Admin + Editorial Head',
   'No immediate legal action — document for pattern analysis. If coordinated: IT Act 2000 S.66 harassment',
   'Root cause identified + platform notified if coordinated + monitoring active for 7 days'),

  ('VIDEO_COUNT_DROP',
   'One or more videos deleted from Example Broadcaster channel — could be unauthorized deletion, hacking, or accidental removal',
   '["URGENT: Check YouTube Studio → Content → Deleted videos immediately","If unauthorized deletion: CHANGE ALL PASSWORDS IMMEDIATELY","Enable 2-step verification if not active","Check for unauthorized admin accounts in YouTube Studio → Settings → Permissions","File CERT-In report within 6 hours (mandatory breach notification)","Attempt to restore from YouTube''s 21-day recovery window","Contact YouTube Partner Support for emergency recovery","Backup all remaining videos to NAS archive"]',
   'IT Admin (IMMEDIATE) + CEO',
   'CERT-In Directions 2022 — mandatory report within 6 hours. IT Act 2000 S.43 if malicious deletion',
   'Videos restored or documented as unrecoverable + credentials secured + CERT-In filed + backup verified'),

  ('VIDEO_COUNT_SPIKE',
   'Unusual bulk video upload detected — could indicate account compromise with malicious content or unauthorized access',
   '["Open YouTube Studio → Content and review all recently uploaded videos","If unauthorized uploads: remove immediately (do NOT leave up)","Change all YouTube account passwords immediately","Revoke all third-party app access in Google Account settings","Check admin devices for malware","File CERT-In if account was clearly compromised","Review who has upload access in YouTube Studio → Permissions"]',
   'IT Admin',
   'IT Act 2000 S.43 if malicious access. Platform ToS violation if spam content uploaded',
   'Unauthorized content removed + credentials secured + access audit complete'),

  ('ANCHOR_IMPERSONATION',
   'Social media account impersonating a Example Broadcaster anchor or journalist — misleading audience with fake content attributed to real staff',
   '["Screenshot all evidence (profile, posts, URL) immediately — accounts get deleted quickly","Report to platform using official impersonation report form: Instagram/Facebook/X/YouTube","Inform the impersonated anchor so they can report personally (platform prioritizes victim reports)","Issue public warning on official Example Broadcaster channels identifying the fake account","Add anchor name and likeness to Kaaval monitoring probe library","If defamatory content posted: preserve evidence for legal action","File police complaint if financial fraud or serious defamation involved"]',
   'Editorial Head + Legal + impersonated anchor directly',
   'IT Act 2000 S.66C (identity theft) + S.66D (cheating by impersonation). Cognizable offence — FIR can be filed',
   'Platform report filed + public warning issued + account removed or labeled fake + anchor informed'),

  ('INSTAGRAM_MISUSE',
   'Instagram account misusing Example Broadcaster branding, footage, or anchor likeness without authorization',
   '["Document evidence with full-screen screenshots and video recording","Report via Instagram Help Center → Report → Impersonation or Copyright","If using Example Broadcaster footage: file copyright report at instagram.com/legal/copyright","Add account to Kaaval monitoring watchlist","If account has large following: issue public clarification on official @examplebroadcaster","Track if content spreads to other platforms"]',
   'Legal + Editorial Head',
   'Copyright Act 1957 if footage used. Trademark Act 1999 if logo misused. IT Act 2000 S.66C if impersonation',
   'Report filed + content removed or account actioned by Instagram + documented in audit trail'),

  ('BRIGADE',
   'Coordinated comment attack detected — multiple accounts posting negative or inflammatory comments in a short time window to damage Example Broadcaster''s YouTube algorithm ranking',
   '["Open YouTube Studio → Comments and filter by Recent","Hold comments from flagged accounts for review","Bulk-report accounts to YouTube Trust & Safety with evidence of coordination","Enable comment moderation temporarily on affected video","Add detected keywords to YouTube comment filter → Settings → Community","Document account patterns (usernames, timestamps, similar text) for YouTube report","Monitor for re-emergence over next 48 hours","If politically motivated: inform Editorial Head for context"]',
   'IT Admin + Editorial Head',
   'IT Act 2000 S.66 — online harassment if coordinated. Can be reported to CERT-In if it affects service',
   'Comments removed + accounts reported + keyword filter active + re-emergence monitoring for 48h'),

  ('FAKE_PAGE',
   'Fake or impersonator page found using Example Broadcaster network branding (Example Broadcaster, Example News, Example Entertainment, Example Movies) on social media or as a domain',
   '["Document evidence: full screenshot of page URL, follower count, recent posts","Report via platform impersonation form: Facebook/Instagram/YouTube/Telegram","For lookalike domains: send cease-and-desist via Legal and file UDRP complaint if needed","Issue public warning on official Example Broadcaster channels","Add domain/URL to Kaaval fake page monitor","If collecting money or spreading political content: escalate to Legal immediately","Track takedown confirmation — follow up after 7 days if no action"]',
   'Legal + IT Admin',
   'Trademark Act 1999 + IT Act 2000 S.66C. For lookalike domains: UDRP (domain dispute) available',
   'Platform report filed + page taken down or labeled + domain complaint filed if applicable')

ON CONFLICT (threat_type) DO UPDATE SET
  what_it_means        = EXCLUDED.what_it_means,
  remediation_steps    = EXCLUDED.remediation_steps,
  who_to_contact       = EXCLUDED.who_to_contact,
  legal_implications   = EXCLUDED.legal_implications,
  resolution_criteria  = EXCLUDED.resolution_criteria;
