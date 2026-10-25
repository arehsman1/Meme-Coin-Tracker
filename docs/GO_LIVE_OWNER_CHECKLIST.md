# Go-live checklist (owner)

Alert-only production. **Trading stays OFF.**

## 1. Database

- [ ] Create Supabase project
- [ ] **Option A (easiest):** paste `supabase/ALL_MIGRATIONS_COMBINED.sql` into the SQL Editor → Run
- [ ] **Option B:** apply in order:
  1. `supabase/migrations/20251018000001_initial_schema.sql`
  2. `supabase/migrations/20251018000002_roles_rls_and_extensions.sql`
  3. `supabase/migrations/20251019000003_wallet_intelligence.sql`
  4. `supabase/migrations/20251019000004_wallet_performance.sql`
- [ ] Confirm tables exist (`tokens`, `tracked_wallets`, `notification_outbox`, …)

## 2. Secrets (server only)

- [ ] `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (never in Mini App)
- [ ] `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ADMIN_IDS`
- [ ] Optional: `HELIUS_API_KEY`, Alchemy / RPC URLs for Solana, Ethereum, Base

## 3. Telegram

- [ ] BotFather token
- [ ] Mini App HTTPS URL registered as Web App
- [ ] Admin numeric Telegram user IDs in `TELEGRAM_ADMIN_IDS`

## 4. Deploy

```bash
cp .env.example deploy/compose/.env   # fill values
cd deploy/compose && docker compose up -d --build
./scripts/verify_deploy.sh https://your-api-host
```

- [ ] `/health` and `/ready` OK
- [ ] `live_trading` is **false** on `/api/v1/settings/public`
- [ ] Bot `/start` responds
- [ ] Mini App opens in Telegram

## 5. Smoke (alert-only)

- [ ] `/token solana <mint>` or Mini App evaluate
- [ ] Track one **public** wallet (no personal wallet connect)
- [ ] Confirm no trading / signing flags are ON

## Explicit non-goals for this go-live

- Personal wallet connection  
- Auto-buy / auto-sell  
- Wallet signing  

Those require a future release that sets `TRADING_FEATURE_ENABLED` and ships real execution code — not a settings toggle alone.
