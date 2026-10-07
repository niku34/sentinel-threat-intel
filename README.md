# Sentinel — Multi-Agent Threat Intelligence Platform
## Unified AI Security Platform for Broadcast Media
### Reference implementation: multi-agent threat intelligence for a broadcast network

---

## Overview

Sentinel is an on-premises AI security platform purpose-built for broadcast media, protecting Example Broadcaster from deepfakes, impersonation, footage theft, and coordinated social media attacks. It combines real-time threat intelligence, automated compliance drafting, and newsroom fact-checking into a single unified dashboard. All storage and all LLM inference run locally; the platform does make outbound calls to public sources it monitors (see Data Residency). The platform operates across three modules: **Kaaval AI** (threat guardian), **Digital Kavasam** (broadcast shield), and **Veriff** (fact-checking engine).

**Key stats:** 6 named agents · 7 fact-check sources · 4 YouTube channels monitored

---

## Architecture

### Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (React, TypeScript) |
| Backend API | FastAPI, Python 3.14 |
| AI Agents | Python 3.11 |
| LLM | Ollama · Qwen 2.5 14B (on-premises) |
| Database | PostgreSQL 18 + pgvector |
| Auth | JWT + bcrypt (**MFA not implemented** — see Security Status) |

### Data Residency

**On-premises:** all storage (PostgreSQL), all LLM inference (Ollama · Qwen 2.5 14B), the API, and the dashboard. **No data is sent to any external AI provider.** No staff personal data and no broadcast footage leaves the network.

**Leaves the network.** Sentinel monitors public sources, so it necessarily talks to them:

| Outbound call | What is sent |
|---|---|
| 7 fact-check sources | **Claim text**, as a search query — this is editorial content |
| Google News / Google Search | Claim-derived search terms |
| YouTube Data API | Channel IDs; returns public counts |
| Meta Graph API | Page / Instagram account IDs (only when configured) |
| HuggingFace | Embedding model weights, first run only, cached after |

### Security Status

Sentinel is **working towards** DPDP Act 2023 compliance and should not be described as compliant yet. Outstanding items that block a Section 17 claim:

- **No TLS** — the API and dashboard are served over plain HTTP
- **No MFA** — login is password-only; the `users.mfa_secret` column is unused
- **No API key rotation** and **no retention purge job**

Live status for every control: `GET /api/security/posture`. Full assessment: `infrastructure/DPDP-compliance.md`.

### Virtual Environments

Two isolated Python environments are required:

| Path | Python | Purpose |
|---|---|---|
| `api/.venv` | Python 3.14 | FastAPI backend + all routers |
| `agents/.venv` | Python 3.11 | AI agents (sentence-transformers, torch) |

---

## Design Mockup — Not a Data Source

`DESIGN_MOCKUP_do-not-copy-data.html` (formerly `Sentinel_Demo_3.html`) is a
static sales mockup from 16 June 2026. **Every number in it is fictional.** It is
kept for visual reference — typography, spacing, colour tokens, component
structure — and nothing else.

During the August 2026 accuracy audit, most fabricated figures found in the live
product traced back to this file: invented uptime percentages, a fabricated
security score, a "147 sources monitored" count with no basis, invented
day-over-day deltas, and two narratives that had been pasted into the production
database as a CERT-In draft and a set of brand-misuse threats.

When you need a value for a page, read it from an API endpoint. If no endpoint
can supply it, show nothing rather than borrowing a figure from the mockup.

---

## Quick Start

### Prerequisites

- Ubuntu 22.04+
- Python 3.11 and Python 3.14
- Node.js 18+
- PostgreSQL 18
- Ollama with `qwen2.5:14b` pulled (`ollama pull qwen2.5:14b`)

### Installation

```bash
# 1. Clone or copy the repo to the server
git clone <repo-url> ~/sentinel
cd ~/sentinel

# 2. Configure environment
cp env.example api/.env
nano api/.env  # fill in DATABASE_URL, JWT_SECRET_KEY, YOUTUBE_API_KEY

# 3. Deploy
./deploy.sh
```

`deploy.sh` will: verify requirements → check `.env` → create database → set up both venvs → build dashboard → start API and frontend.

### Manual Start (development)

```bash
# API
cd api && nohup .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload > nohup.out 2>&1 &

# Dashboard
cd dashboard && nohup npm run dev > nohup.out 2>&1 &
```

Dashboard: http://localhost:3000 · API: http://localhost:8000 · API Docs: http://localhost:8000/docs

---

## Modules

### Kaaval AI — காவல் (The Guardian)

Tamil broadcast threat intelligence engine. Kaaval monitors Example Broadcaster's social presence for emerging threats and generates editorial-ready intelligence briefs.

**Detects:**
- Deepfake audio and video of anchors
- Fake pages impersonating Example Broadcaster branding
- Brigade attacks on YouTube comment sections
- Anchor voice impersonation (Kaaval voice fingerprint library)
- Archive footage misuse and out-of-context recycling

**Agents:** 6 named agents report status (Kaaval AI, Digital Kavasam, Veriff, Judge LLM, CERT-In Drafter, Audit Logger), implemented across ~23 Python modules. Live status: `GET /api/scan/status`.

**Accuracy:** 75–85% — **estimated, not measured.** A design target, not a result against a labelled dataset. Requires editorial confirm/dismiss feedback to become a real figure.

---

### Digital Kavasam — கவசம் (The Shield)

YouTube and social account protection suite. Kavasam handles compliance automation and channel health monitoring for the Example Broadcaster.

**4 channels monitored:**
- Example Broadcaster
- Example News
- Example Entertainment
- Example Movies

**Features:**
- CERT-In 6-hour incident report auto-drafter (CERT-In Directions 2022)
- DPDP 180-day tamper-evident audit trail
- YouTube channel health scoring
- Meta Rights Manager integration (reference files + strike tracker)
- Kill Switch — instant session revocation on login anomaly

**Accuracy:** 99% — **estimated, not measured.** Reflects the expected reliability of API-based detection, not an observed rate.

---

### Veriff — நேர்மை (Truth)

Real-time fact-checking engine for Example News newsroom journalists. Veriff scrapes 7 Tamil and national fact-check sources and returns a verdict with confidence score in under 30 seconds.

**7 fact-check sources:**
1. PIB Fact Check
2. AltNews
3. Vishvas News
4. Fact Crescendo Tamil
5. India Today Fact Check
6. Quint WebQoof
7. Newschecker Tamil

**Features:**
- pgvector semantic caching — repeat claims served from cache (`from_cache: true`)
- sentence-transformers all-MiniLM-L6-v2 (384-dim embeddings)
- Google Vision integration for image forensics (PIL fallback if credentials not set)
- Video provenance: keyframe/metadata extraction via yt-dlp. **Reverse-video search is not available** — the InVID API host does not resolve, so no earlier-appearance matching runs.

**Verdict time:** < 30 seconds (target; the measured average is reported live on the Accuracy dashboard)

**Accuracy:** 70–78% — **estimated, not measured.** Weakest module; target is 85% after the semantic matching upgrade.

---

## Configuration

### Environment Variables

See `env.example` for the full list. Key variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `JWT_SECRET_KEY` | JWT signing key (minimum 32 characters) |
| `OLLAMA_BASE_URL` | Ollama endpoint (default: `http://localhost:11434`) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key |
| `APP_ENCRYPTION_KEY` | pgcrypto key for encrypting email/MFA fields |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to Google Vision service account JSON (optional) |
| `ARCHIVE_STORAGE_PATH` | Local path for broadcast archive storage |

### API Keys (enter via Admin Console)

Sensitive credentials are stored in the `platform_config` database table, not in `.env`. Manage them at **Admin Console → API Key Vault**.

| Key | Used By |
|---|---|
| YouTube Data API v3 | Digital Kavasam — channel monitoring |
| Meta App ID + Secret | Facebook/Instagram monitoring (optional) |
| Google Vision credentials | Image analysis in Veriff (optional, PIL fallback active) |

---

## Database

### Setup

```bash
# Run as postgres superuser — creates DB, user, schema, all migrations, and permissions
sudo -u postgres psql -f database/db_setup.sql
```

### Migrations

Individual migration files in `database/migrations/`:

| File | Description |
|---|---|
| `schema.sql` | Base schema — users, channels, scans, findings, alerts, plugins, audit_log |
| `004_finding_playbooks.sql` | Threat playbooks with remediation steps |
| `006_plugins_fix_constraints.sql` | Plugin catalogue seed (Kaaval, Kavasam, Veriff) |
| `007_anchor_voice_profiles.sql` | Anchor voice fingerprint library |
| `008_meta_rights_manager.sql` | Meta reference files and strike tracker |
| `009_claim_embeddings.sql` | pgvector claim cache for semantic fact-checking |

`database/db_setup.sql` runs all of the above in order — use it for fresh installs.

---

## Security

### Compliance

| Regulation | Implementation |
|---|---|
| DPDP Act 2023 | 180-day audit log retention, encrypted PII (pgcrypto) |
| CERT-In Directions 2022 | 6-hour incident report auto-drafter |
| IT Act 2000 S.43A | Documented security practices, bcrypt passwords, TOTP MFA |

### Network (recommended)

- All LLM inference runs on-premises via Ollama — no cloud API calls
- Outbound connections: fact-check scraper APIs and YouTube Data API only
- Recommended: DMZ Nginx/Squid proxy between scrapers and internal database
- Secure Ollama port 11434 with bearer token via Nginx reverse proxy on 11435

See `infrastructure/README-security.md` for full hardening steps: Nginx config, firewall rules, Squid DMZ proxy, and mTLS setup guide.

### Human-in-the-Loop (non-negotiable)

Sentinel detects and drafts. Humans decide and file.

- No auto-strikes issued to platforms
- No auto-DMCA takedowns filed
- No auto-CERT-In reports submitted
- Every action requires explicit editorial confirmation in the dashboard

---

## Credentials & Key Management

### Where credentials are stored

All platform API keys and secrets are stored in the `platform_config` database table. They are never hardcoded in source files. Manage them from **Admin Console → API Key Vault**.

### Rotating keys

**Admin Console → API Key Vault → ROTATE** (next to any key)

JWT secret rotation requires restarting the API (`kill $(lsof -ti:8000)` then restart via `start.sh`). All active sessions will be invalidated on rotation.

---

## Troubleshooting

### API not starting

```bash
cat ~/sentinel/api/nohup.out | tail -20
```

Check that PostgreSQL is running and `DATABASE_URL` in `api/.env` is correct.

### Dashboard blank screen

```bash
cd ~/sentinel/dashboard && rm -rf .next && npm run dev
```

### Agents venv issues

```bash
rm -rf ~/sentinel/agents/.venv
cd ~/sentinel/agents && python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Database connection failed

```bash
sudo systemctl start postgresql
curl -s http://localhost:8000/health | python3 -m json.tool
```

### Ollama not responding

```bash
ollama serve &
ollama pull qwen2.5:14b
curl http://localhost:11434/api/tags
```

### Port already in use

```bash
kill $(lsof -ti:8000)
kill $(lsof -ti:3000)
```

---

## Support & Contacts

| Role | Name | Contact |
|---|---|---|
| Developer | Nitin Pranav | github.com/niku34 |


---


