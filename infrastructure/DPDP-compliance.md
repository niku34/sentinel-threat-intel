# DPDP Act 2023 Compliance — Sentinel
## Example Broadcaster

---

## Status of this document

This document records the **actual** data protection posture of Sentinel as
deployed, including controls that are **not yet implemented**. It is a working
assessment, not a certification.

Sentinel is *working towards* DPDP Act 2023 compliance. Several Section 17
safeguards are outstanding and are listed explicitly below. **This platform
should not be represented as DPDP-compliant until the gaps in "Outstanding
Security Gaps" are closed.**

Every claim marked **Verified** below is checked at runtime by
`GET /api/security/posture`, which returns `{status, verified, note}` per control
and reports failures rather than assuming success. Where this document and that
endpoint disagree, the endpoint is authoritative.

---

## Data Inventory

| Data Type | Where Stored | Purpose | Retention |
|---|---|---|---|
| Login credentials (2 accounts) | Hardcoded `_USERS` dict in `api/routers/auth.py` (bcrypt hashes) | Authentication | Until code change |
| Team member records | `api/data/team_members.json` — **plaintext email** | User management UI | Indefinite |
| `users` table (pgcrypto columns) | `users` table — **not currently used by the auth path** | Reserved for DB-backed auth | n/a |
| Session tokens | In-memory (`active_sessions`) — lost on API restart | Access control | 8 hours (JWT expiry) |
| Audit log entries | `audit_log` table, WORM-protected | Compliance | Indefinite (see retention) |
| Fact-check submissions | `fact_checks` table | Verification, editorial due diligence | Unenforced |
| Threat intelligence | `threats` table | Security monitoring | Unenforced |
| Voice fingerprints | `anchor_voice_profiles` table | Deepfake detection | Indefinite |
| YouTube channel data | `channel_snapshots` table | Anomaly detection | Unenforced |

**Notes:**
- The `users` table does provide pgcrypto-encrypted `email` (BYTEA) and an
  `email_hash` lookup column, but **the live authentication path does not read
  it**. Logins are served from a two-entry hardcoded dict, and the User
  Management screen is backed by a JSON file that stores **email addresses in
  plaintext**. Migrating auth to the `users` table is prerequisite to claiming
  encrypted-at-rest for personal data.
- `audit_log.ip_address` is a **TEXT column storing plaintext IP addresses**
  (e.g. `127.0.0.1`). It is not encrypted BYTEA.

---

## DPDP Obligations Mapping

| DPDP Section | Obligation | Sentinel Implementation | Status |
|---|---|---|---|
| Section 4 | Lawful Processing | Security monitoring under legitimate interest of a broadcast organisation | Compliant |
| Section 6 | Consent | Internal platform, Example Broadcaster staff only — B2B use case, not consumer | N/A (B2B) |
| Section 8 | Data Fiduciary duties | Example Broadcaster Ltd is the Data Fiduciary; Example Broadcaster is the principal employer | Documented |
| Section 9 | Children's data | Internal staff platform; login-gated (password only — **no MFA**) | N/A |
| Section 11 | Right to information | Admin Console → User Management shows data held per user (JSON-backed) | Partial |
| Section 12 | Right to correction | User Management provides create / update-role / activate / deactivate | Partial |
| Section 13 | Right to erasure | Deactivation only; no deletion endpoint | **Not met** |
| Section 17 | Security safeguards | See "Outstanding Security Gaps" — TLS and MFA are absent | **Not met** |

---

## Outstanding Security Gaps (Section 17)

These are the controls that block a Section 17 compliance claim. Each is
verifiable via `GET /api/security/posture`.

| Gap | Current state | Required for compliance |
|---|---|---|
| **No transport encryption** | API and dashboard are served over plain HTTP. No TLS termination is deployed. | Nginx TLS 1.3 termination + HSTS before any non-localhost deployment |
| **No multi-factor authentication** | `/api/auth/login` verifies a password only. The `users.mfa_secret` column exists but is never read or validated. | TOTP enrolment + verification in the login flow |
| **No API key rotation** | No rotation timestamps recorded in `platform_config`; credentials have never been rotated. | Rotation schedule + recorded rotation events |
| **Full-disk encryption not applied** | pgcrypto column encryption is available but unused by the live auth path; LUKS is not enabled. | LUKS on the production volume + migrate auth to the `users` table |
| **No API-layer authorisation** | Role-based access is enforced at the database level (RLS policies, 2 DB roles). Individual API endpoints do not check role. | Per-endpoint role checks |
| **Plaintext IPs in audit log** | `audit_log.ip_address` stores IPs as TEXT. | Encrypt at write time, or document as non-personal under the deployment model |

---

## Implemented Security Measures

Controls below are implemented and runtime-verifiable.

| Measure | Implementation | Verification |
|---|---|---|
| Tamper-evident audit log | `audit_log_worm_trigger` rejects UPDATE and DELETE on `audit_log`; SHA-256 hash chain over entries | **Verified** — `GET /api/audit/verify-integrity` walks the chain; posture check `tamper_proof_logging` |
| Audit retention | No purge path exists — the WORM trigger makes deletion impossible, so entries are retained indefinitely | **Verified** — posture check `audit_retention_180d` |
| Database-level access control | 6 row-level security policies across 2 database roles (`sentinel_app`, `sentinel_readonly`); `users.role` constrained to admin / analyst / viewer | **Verified** — posture check `rbac` |
| Password storage | bcrypt hashes; no plaintext passwords stored or logged | Code review — `core/auth.py` |
| Rate limiting | Per-JWT-token keying via `slowapi`, falling back to IP; prevents shared-newsroom-IP lockouts | Code review — `core/limiter.py` |
| Login anomaly response | Scores each login on new-IP / unusual-hour (outside 06:00–23:00 IST) / session-flood (≥3 concurrent). A CRITICAL score (session flood) revokes the user's other sessions and writes an `ANOMALY_KILL_SWITCH` audit entry | Code review — `routers/auth.py` |

**Note on the two "Kill Switch" features** — these are distinct and were
previously conflated:
1. **Agent Kill Switch** (`POST /api/admin/kill-switch`) — pauses agent scans by
   writing a state flag. It does **not** revoke user sessions.
2. **Login anomaly revocation** (`routers/auth.py`) — revokes a user's other
   sessions on a CRITICAL-risk login. This is the session-revocation control.

---

## Breach Notification (CERT-In + DPDP)

The CERT-In drafter reduces the time to produce a report; it does not by itself
satisfy the notification obligation, which requires a human to review and file.

| Stage | Implementation | Reality |
|---|---|---|
| Detection — account | Login anomaly detection in `routers/auth.py` (new IP, unusual hour, session flood). **Limitation:** baselines come from the in-memory session store, so known-IP history resets on every API restart | Implemented |
| Detection — channel | Digital Kavasam YouTube channel anomaly detection. **Note:** this monitors YouTube channel metrics, not logins or personal data | Implemented; tuning ongoing |
| Alert | Session revocation on CRITICAL login anomaly + WebSocket event to connected dashboards | Implemented |
| Report generation | `POST /api/certin/drafts` produces a pre-filled CERT-In report. Kavasam can trigger a draft from a channel anomaly | Implemented — **drafts only, never auto-filed** |
| Filing | **Manual.** A human reviews the draft and files it with CERT-In | Not automated |
| DPDP notification | Same report template covers the DPDP breach notification fields | Manual |
| Audit trail | Every action logged with timestamp, user ID, IP — SHA-256 hash chain, WORM-protected | Implemented |

---

## Data Retention Policy

| Data Category | Policy | Enforcement | Reality |
|---|---|---|---|
| Audit logs | Retain ≥180 days | DB-enforced — WORM trigger blocks deletion | **Enforced.** Entries are retained indefinitely; there is no purge path |
| Fact-check records | 180 days | — | **Not enforced.** No purge job exists |
| Threat intelligence | 180 days | — | **Not enforced.** No purge job exists |
| Channel snapshots | 30 days | — | **Not enforced.** No purge job exists |
| Session tokens | 8 hours | JWT `ACCESS_TOKEN_EXPIRE_MINUTES=480` | Enforced by token expiry |
| Voice fingerprints | Indefinite | Operational necessity | As stated |

**There is no scheduled retention job in the platform** — no cron entry, no
systemd timer, no application scheduler. The three "not enforced" categories
above will grow without bound until a purge job is implemented.

---

## Data Residency and External Data Flows

Storage and LLM inference are on-premises. **The platform does make outbound
calls to external services**, listed in full below — the earlier "zero cloud
exposure" phrasing was inaccurate.

| Component | Runs On | External Data Exposure |
|---|---|---|
| PostgreSQL 18 database | On-premises server | None |
| Ollama LLM (Qwen 2.5 14B) | On-premises server | **None** — no prompts or content sent to any external AI provider |
| Next.js dashboard | On-premises server | None |
| FastAPI backend | On-premises server | None |
| YouTube Data API | External (googleapis.com) | Channel IDs; returns public subscriber/video counts |
| Fact-check scrapers (7 sources) | External | **Claim text is submitted as a search query** to public fact-check sites — this is editorial content leaving the network |
| Google News / Google Search | External | Claim-derived search terms |
| Meta Graph API | External | Page/Instagram account IDs, when Meta integration is configured |
| Sentence-transformers model | External on first run (HuggingFace) | Model weights download only; cached locally thereafter. No claim data sent |

Personal data of Example Broadcaster staff does not leave the network. Editorial claim text
does, by design, when a journalist runs a fact check.

---

## Recommended Actions (Pre-Production)

| Priority | Action | Owner | Notes |
|---|---|---|---|
| 1 | **Deploy TLS 1.3 termination** | IT Admin | Blocks Section 17. No production deployment over plain HTTP |
| 2 | **Implement MFA** | Nitin Pranav | Blocks Section 17. `users.mfa_secret` column already exists |
| 3 | Migrate authentication to the `users` table | Nitin Pranav | Removes the hardcoded dict and the plaintext-email JSON file |
| 4 | Implement Right to Erasure endpoint | Nitin Pranav | `DELETE /api/users/{id}` — Section 13 currently not met |
| 5 | Implement retention purge job | Nitin Pranav | Fact checks, threats, channel snapshots — currently unbounded |
| 6 | Appoint Data Protection Officer (DPO) | Organisation management | Required for Data Fiduciary under DPDP Act |
| 7 | Complete Data Protection Impact Assessment (DPIA) | DPO + Nitin Pranav | Before go-live |
| 8 | Register with Data Protection Board | DPO | When Board becomes operational |
| 9 | Enable LUKS full-disk encryption | IT Admin | On production server before go-live |
| 10 | Commission external penetration test | Organisation management | **Not yet scheduled.** No test has been booked or performed |
| 11 | Document consent mechanism | DPO | For any external data subjects if platform scope expands |

**Completed:** WORM audit-log migration (`010_worm_audit_log.sql`) is applied —
`audit_log_worm_trigger` is present and verified.

---

*Document prepared by: the project author*
*Date: July 2026 · Revised August 2026 following a platform-wide accuracy audit*
*Review cycle: Quarterly*
*Runtime verification: `GET /api/security/posture`*
