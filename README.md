# Meme Coin Tracker

Multi-chain token scanner, scam-risk intelligence, qualification engine, Telegram bot, and Telegram Mini App.

**Supported chains (v0.1):** Solana mainnet, Ethereum mainnet, Base mainnet.

**Live trading, automatic buys/sells, and wallet signing are permanently OFF in this release.**

---

## What this system does

1. Discovers newly created tokens on Solana, Ethereum, and Base.
2. Collects evidence (identity, permissions, liquidity, holders, sellability signals).
3. Runs a single authoritative qualification engine that produces deterministic statuses.
4. Persists every evaluation, score, and status transition in Supabase.
5. Continuously monitors qualified tokens and can revoke qualification on material risk.
6. Sends Telegram alerts only for fully qualified trade candidates (and risk events).
7. Provides a Telegram Mini App for the owner to inspect tokens, watchlists, and service health.

The system prioritises verifiable evidence and mandatory safety gates over alert volume.

---

## Architecture

### Discovery

Periodic DexScreener polling finds recent pairs on Solana, Ethereum, and Base.
New addresses are enqueued for evaluation. Discovery alone never triggers a qualification alert.
Manual trigger: `POST /api/v1/discovery/run`


| Component | Language | Responsibility |
|-----------|----------|----------------|
| Qualification engine | Python | Authoritative policy, scores, status transitions |
| API | Python (FastAPI) | REST API, auth, admin controls |
| Worker | Python | Scheduling, re-evaluation, outcome collection |
| Telegram bot | Python | Commands + notification delivery |
| Scanner / ingestion | Rust (`meme_scanner`) | DexScreener poll + enqueue evaluate jobs |
| Mini App | TypeScript + React | Owner dashboard & controls |
| Database | Supabase (PostgreSQL) | All persistent state + RLS |

---

## Current implementation status (honest)

| Area | Status |
|------|--------|
| Schemas / migrations 000001–000004 | Implemented (apply on live Supabase) |
| Qualification engine | Implemented + tested |
| Wallet Intelligence + performance | Implemented + Mini App screens |
| Manipulation risk module | Implemented |
| Discovery (DexScreener) | Implemented; live depth needs RPC keys |
| Telegram bot commands | Implemented; needs bot token |
| Mini App | Implemented; needs HTTPS + BotFather |
| Worker (discovery, outcomes, revocation, snapshots) | Implemented |
| API security (rate limit, headers) | Implemented |
| Trading / wallet signing | **Hard-blocked OFF** (Phase 11 gates) |
| Offline E2E + CI | Implemented |
| Live deploy verification | **Owner** — see `docs/GO_LIVE_OWNER_CHECKLIST.md` |

**Tests:** run `PYTHONPATH=services/api/src python -m pytest tests/unit tests/integration -q`

**Go-live (you):**

1. `./scripts/print_migrations.sh` then apply SQL on Supabase  
2. Fill `deploy/compose/.env` from `.env.example`  
3. `docker compose up -d --build` under `deploy/compose`  
4. `./scripts/verify_deploy.sh https://your-api-host`  
5. BotFather + Mini App HTTPS URL  

Full checklist: [`docs/GO_LIVE_OWNER_CHECKLIST.md`](docs/GO_LIVE_OWNER_CHECKLIST.md) · Deploy detail: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)

---

## Repository layout (what goes on GitHub)

This is **one monorepo** (not two separate products). Push the whole folder.

```
.
├── services/api/          # Python backend (API + qualification + worker + bot code)
├── crates/                # Rust scanner workspace (optional high-throughput discovery)
├── apps/miniapp/          # Telegram Mini App (React + Vite) — the web UI
├── supabase/              # SQL migrations (+ ALL_MIGRATIONS_COMBINED.sql)
├── deploy/                # Docker Compose, Dockerfiles; Render/Replit placeholders
├── docs/                  # DEPLOYMENT, GO_LIVE checklist, security
├── scripts/               # verify_deploy.sh, print_migrations.sh
├── tests/                 # unit + offline integration
├── .env.example           # copy to .env — never commit real secrets
└── README.md
```

| Folder | Role |
|--------|------|
| `services/api` | **Backend** — FastAPI, worker, Telegram bot, engine |
| `crates/*` | **Rust** scanner (same repo; optional) |
| `apps/miniapp` | **Web / Mini App** UI |

Backend + Rust live under the same repo root; the Mini App is a separate folder (`apps/miniapp`) but ships in the **same** GitHub repository.

### Push to GitHub

```bash
cd /path/to/this/project
git init
git add .
git status   # confirm .env is NOT listed
git commit -m "Initial Meme Coin Tracker (alert-only)"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Never commit `.env` or service-role / bot tokens.

---

## What to install (by platform)

### Shared (all platforms)

| Requirement | Notes |
|-------------|--------|
| Supabase project | Free tier OK to start |
| Telegram bot | @BotFather token + your numeric user id |
| HTTPS URL for Mini App | **Required** by Telegram (see Domain section below) |
| Optional RPC keys | Helius / Alchemy for deeper chain data |

### VPS (recommended for 24/7)

Install on the server:

- Docker + Docker Compose v2  
- (Optional) Caddy or nginx for HTTPS  
- Git  

```bash
git clone https://github.com/YOUR_USER/YOUR_REPO.git
cd YOUR_REPO
cp .env.example deploy/compose/.env
# edit deploy/compose/.env
cd deploy/compose
docker compose up -d --build
```

Services: API `:8000`, Mini App `:8080`, plus worker and telegram-bot containers.

### Render

- Create a **Web Service** from this repo for the API (`services/api`, start: `uvicorn meme_tracker.api.main:app --host 0.0.0.0 --port $PORT`).
- Add a **Background Worker** with the same image/env running `python -m meme_tracker.worker.main`.
- Static site or second service for `apps/miniapp` build output.
- Set env vars from `.env.example` in the Render dashboard.
- Free tier may sleep — not ideal for continuous scanning.

### Replit

- Import the repo; use Python 3.12.
- Run API in one process; worker in another (or same Replit if you accept limits).
- Mini App: build static files or use Vite dev only for tests.
- Replit HTTPS URL can be used as `MINI_APP_URL` for experiments; not ideal for production scanning.

---

## Domain and IP (no custom domain?)

| Goal | Works? |
|------|--------|
| API on `http://YOUR_VPS_IP:8000` | Yes for curl / testing |
| Mini App on `http://YOUR_VPS_IP:8080` **inside Telegram** | **No** — Telegram requires **HTTPS** |
| Mini App on `https://something...` | **Yes** |

**You do not need to buy a domain**, but you **do** need HTTPS:

1. **Cloudflare Tunnel** (free) → gives `https://….trycloudflare.com`  
2. **ngrok** / similar tunnel → temporary HTTPS URL  
3. Free subdomain (DuckDNS, etc.) + **Caddy** + Let’s Encrypt on the VPS  
4. Later: point a real domain at the VPS

Set:

```env
MINI_APP_URL=https://your-https-host
VITE_API_BASE_URL=https://your-api-https-host
```

Using only a raw IP with `http://` is fine for local API checks; it is **not** enough for the Telegram Mini App.

---

## Quick start (local development)

### 1. Prerequisites

- Python 3.12+
- Node 20+ (for Mini App later)
- Docker (optional)
- A Supabase project
- Telegram bot token (from @BotFather)

### 2. Clone & configure

```bash
cp .env.example .env
# Edit .env with your Supabase URL, service-role key, and Telegram bot token
```

### 3. Apply database migrations

In the Supabase SQL Editor (or via CLI):

```bash
# Using Supabase CLI (recommended)
supabase db push
# or paste supabase/ALL_MIGRATIONS_COMBINED.sql in the SQL Editor (all 4 migrations)
```

Verify the singleton row exists:

```sql
SELECT * FROM scanner_service_settings;
SELECT * FROM scanner_chain_settings;
```

### 4. Install Python package & run tests

```bash
cd services/api
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest ../../tests/unit -v
```

### 5. Start the API

```bash
cd services/api
uvicorn meme_tracker.api.main:app --reload --host 0.0.0.0 --port 8000
```

Health check: `curl http://localhost:8000/health`

---

## Environment variables

See `.env.example`. Required for a working installation:

| Variable | Required | Purpose |
|----------|----------|---------|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Server-side DB access (never expose to browser) |
| `TELEGRAM_BOT_TOKEN` | Yes | Bot API token |
| `TELEGRAM_ADMIN_IDS` | Yes | Comma-separated Telegram user IDs allowed to control scanners |
| `MINI_APP_URL` | Yes for Mini App | Public HTTPS URL of the Mini App |
| `SOLANA_RPC_URL` / `HELIUS_API_KEY` | For Solana scanning | RPC / enhanced API |
| `ETHEREUM_RPC_URL` / `ALCHEMY_API_KEY` | For Ethereum | RPC |
| `BASE_RPC_URL` | For Base | RPC |

---

## Default service settings (fresh install)

| Setting | Default |
|---------|---------|
| Continuous scanning | ON |
| Automatic scam monitoring | ON |
| Solana / Ethereum / Base scanners | ON |
| Historical outcome collection | ON |
| Telegram qualification alerts | ON |
| Telegram risk alerts | ON |
| Live trading / auto buy / auto sell / wallet signing | **OFF** |

These are the *desired* states. Actual operational state depends on credentials and worker health and is reported separately.

---

## Qualification statuses

| Status | Meaning |
|--------|---------|
| `REJECTED_CRITICAL_RISK` | Mandatory safety gate failed |
| `PENDING_DATA_INCOMPLETE` | Critical evidence missing or unknown |
| `MONITOR_NOT_YET_QUALIFIED` | Concerning but non-critical signals |
| `SECURITY_QUALIFIED_NO_TRADE_SIGNAL` | Safety OK, trade criteria not met |
| `QUALIFIED_TRADE_CANDIDATE` | All gates + trade criteria passed |
| `QUALIFICATION_REVOKED` | Material risk appeared after qualification |

A confirmed critical failure can never be overridden by a high score.

---

## Telegram notification rule

`🆕 NEW TOKEN DETECTED` is sent **only** when:

1. Final status is `QUALIFIED_TRADE_CANDIDATE`
2. Every mandatory safety gate passed
3. Required evidence is fresh and complete
4. Evaluation was persisted successfully
5. The event is not a duplicate
6. Recipient is authorised and has the alert type enabled

Token creation alone never triggers this alert.

---

## Deployment notes

- **VPS (recommended for 24/7 scanning):** Docker Compose under `deploy/compose/`. Configure HTTPS reverse proxy (Caddy or nginx).
- **Render / Replit:** Suitable for development or low-duty testing. Long-running free tiers are not reliable for continuous scanning.
- Continuous scanning starts automatically only after successful deployment **and** valid credentials. Missing provider keys mark the chain as `DEGRADED` or `BLOCKED`.

See **docs/DEPLOYMENT.md** for migration order, Compose usage, BotFather, and `scripts/verify_deploy.sh`.

---

## Security

- Service-role key and bot token stay server-side only.
- Mini App never receives provider secrets.
- Telegram Web App authentication must be validated on the backend (initData HMAC).
- No wallet seed phrases or private keys are ever requested.
- No automatic trading paths exist in this codebase.

---

## Testing

```bash
# Unit tests (qualification engine)
pytest tests/unit -v

# Lint (when ruff is installed)
ruff check services/api/src
```

Live integration tests against real RPCs are intentionally separated and require credentials.

---

## Known limitations (v0.1)

- Full chain depth needs live RPC / indexer keys (without them chains report DEGRADED/BLOCKED).
- Telegram Mini App **requires public HTTPS** (plain `http://IP` will not open inside Telegram).
- No production load testing has been performed.
- Live trading / wallet signing are **hard-blocked OFF** by design.
- Rust scanner: `cargo build -p meme_scanner --release` on a normal host (some sandboxes block build-scripts).

---

## Licence & disclaimer

This software is provided for research and monitoring purposes.  
It does **not** guarantee scam detection, sellability, or profitable trades.  
Assessments are based on available evidence at evaluation time and can be wrong or incomplete.

## Supabase roles & security (migration 2)

Apply both migrations in order:

1. `supabase/migrations/20251018000001_initial_schema.sql`
2. `supabase/migrations/20251018000002_roles_rls_and_extensions.sql`

Bootstrap admins via env:

```bash
TELEGRAM_ADMIN_IDS=123456789,987654321
```

The first ID is treated as **owner** by the API when initData verifies.

Admin API (requires `X-Telegram-Init-Data` with valid HMAC):

- `GET /api/v1/admin/me`
- `GET /api/v1/admin/scanner/settings`
- `PATCH /api/v1/admin/scanner/settings`
- `PATCH /api/v1/admin/chains`

See `docs/SECURITY_SUMMARY.md` and `docs/DATA_DICTIONARY.md`.
