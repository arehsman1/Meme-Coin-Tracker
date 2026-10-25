# Deployment — Meme Coin Tracker

**Live trading, auto-buy/sell, and wallet signing remain OFF.**  
This guide covers API, worker, Telegram bot, Mini App, and Supabase schema.

---

## 1. Prerequisites

| Requirement | Notes |
|-------------|--------|
| Supabase project | PostgreSQL + service-role key (server only) |
| Telegram bot token | From @BotFather |
| Public HTTPS URL | Required for Mini App + Telegram WebApp auth |
| Docker + Compose v2 | For self-hosted stack |
| RPC / indexing keys | Helius, Alchemy, etc. (optional until live scan) |

Never put the service-role key or bot token in the Mini App bundle.

---

## 2. Apply database migrations

Order matters. In Supabase SQL Editor (or `supabase db push`):

1. `supabase/migrations/20251018000001_initial_schema.sql`
2. `supabase/migrations/20251018000002_roles_rls_and_extensions.sql`
3. `supabase/migrations/20251019000003_wallet_intelligence.sql`

Verify:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'tokens', 'qualification_evaluations', 'notification_outbox',
    'tracked_wallets', 'wallet_transactions', 'wallet_profiles'
  )
ORDER BY 1;
```

Confirm scanner defaults:

```sql
SELECT * FROM scanner_service_settings;
SELECT * FROM scanner_chain_settings;
```

---

## 3. Environment

```bash
cp .env.example deploy/compose/.env
# Edit deploy/compose/.env
```

Required for a working stack:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_IDS` (comma-separated numeric Telegram user IDs)
- `MINI_APP_URL` (public HTTPS, must match BotFather Web App URL)

Optional until live scanning:

- `HELIUS_API_KEY` / `SOLANA_RPC_URL`
- `ALCHEMY_API_KEY` / `ETHEREUM_RPC_URL` / `BASE_RPC_URL`

For Mini App production build, set compose arg / env:

- `VITE_API_BASE_URL=https://api.your-domain.com`  
  (empty = same-origin / relative)

---

## 4. Docker Compose (VPS)

From repository root:

```bash
cd deploy/compose
docker compose up -d --build
docker compose ps
docker compose logs -f api worker telegram-bot
```

Default ports:

| Service | Host port |
|---------|-----------|
| API | 8000 |
| Mini App (nginx) | 8080 |

Health:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:8080/
```

Put a reverse proxy (Caddy / nginx / Traefik) in front with TLS:

- `https://api.example.com` → api:8000  
- `https://app.example.com` → miniapp:80  

Set `MINI_APP_URL=https://app.example.com` and BotFather domain accordingly.

---

## 5. Telegram BotFather

1. Create bot → copy token → `TELEGRAM_BOT_TOKEN`
2. **Bot Settings → Menu Button / Web App** → URL = `MINI_APP_URL`
3. Domain must match the HTTPS host serving the Mini App
4. Admin Telegram user IDs → `TELEGRAM_ADMIN_IDS`

Without a public HTTPS Mini App URL, WebApp initData validation still works for bot commands, but the Mini App cannot open inside Telegram.

---

## 6. Post-deploy verification script

```bash
# From repo root, with API reachable
./scripts/verify_deploy.sh http://127.0.0.1:8000
```

The script reports PASS / FAIL / NOT_CONFIGURED for each check.  
It does **not** invent success when Supabase or Telegram is missing.

---

## 7. Safe defaults (enforced)

| Setting | Default |
|---------|---------|
| Personal wallet connection | OFF |
| Automated trading | OFF |
| Live trading / signing | OFF |
| Redis | Not required |
| Token scanning desired | ON (actual state depends on credentials) |

A healthy process is **not** the same as successful live chain scanning.

---

## 8. Rollback / restart

```bash
cd deploy/compose
docker compose restart api worker telegram-bot
# or
docker compose down
docker compose up -d
```

Database state lives in Supabase; containers are disposable.

---

## 9. Owner checklist (cannot be completed in CI alone)

- [ ] Migrations applied on live Supabase  
- [ ] Service-role key only on server  
- [ ] Bot token + admin IDs set  
- [ ] Mini App HTTPS live and registered in BotFather  
- [ ] `/health` and `/ready` green  
- [ ] Manual `/start` on Telegram  
- [ ] Optional: track one public wallet via Mini App or API  
- [ ] Confirm **no** live trading flags enabled  

---

## 10. Known limitations

- Sandbox environments may block `cargo` build-scripts and messy `npm` trees; build Rust scanner and Mini App on a normal host when needed.
- Without RPC keys, discovery/qualification still run in degraded/memory modes for tests; production scanning needs providers.
- Wallet Intelligence tables require migration `20251019000003`.
