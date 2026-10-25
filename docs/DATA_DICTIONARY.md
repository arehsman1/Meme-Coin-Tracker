# Data dictionary — Meme Coin Tracker (Supabase)

## Access control

| Table | Purpose |
|-------|---------|
| `app_users` | App profile linked to `auth.users`; optional `telegram_id` |
| `user_roles` | Roles per user: owner, admin, analyst, user, service_worker, read_only |
| `role_permissions` | Capability matrix (role × permission) |
| `admin_allowlist` | Server-managed Telegram IDs for owner/admin |
| `audit_logs` | Privileged action audit trail |

## Chains & providers

| Table | Purpose |
|-------|---------|
| `chain_registry` | solana / ethereum / base metadata |
| `provider_registry` | Provider name, chain, capabilities, health |
| `provider_health_events` | Health history |

## Tokens & evidence

| Table | Purpose |
|-------|---------|
| `tokens` | Canonical identity `(chain, address)` unique |
| `token_markets` | Pools / markets |
| `token_observations` | Time-stamped evidence payloads |
| `token_permissions` | Mint/freeze/owner/tax flags |
| `holder_snapshots` | Concentration snapshots |
| `liquidity_snapshots` | Pool liquidity |
| `sellability_checks` | VERIFIED / LIMITED / UNKNOWN / FAILED |
| `market_activity_snapshots` | Buy/sell window stats |
| `wallet_entities` / `wallet_relationships` | Entity graph |
| `creator_history` | Deployer history summaries |
| `manipulation_signals` | Risk signals |

## Qualification

| Table | Purpose |
|-------|---------|
| `qualification_policies` | Versioned policy config |
| `qualification_evaluations` | Decision + four scores + revision |
| `qualification_check_results` | PASS/FAIL/UNKNOWN/NOT_APPLICABLE per check |
| `qualification_score_components` | Score breakdown |
| `qualification_status_history` | Status transitions |

## Scanner & jobs

| Table | Purpose |
|-------|---------|
| `scanner_service_settings` | Singleton desired/actual/health (trading defaults OFF) |
| `scanner_chain_settings` | Per-chain desired/actual |
| `scanner_worker_heartbeats` | Worker liveness |
| `scanner_job_runs` | Jobs with lease_owner / lease expiry |
| `scanner_schedules` | Interval schedules |
| `service_state_events` | State transition log |

## Notifications

| Table | Purpose |
|-------|---------|
| `token_watchlists` | Per-user watchlist (RLS: own rows) |
| `alert_preferences` | Per-user alert toggles |
| `notification_outbox` | Durable outbox (dedupe_key unique) |
| `notification_deliveries` | Delivery attempts |
| `risk_alert_events` | Risk events |

## Outcomes & setup

| Table | Purpose |
|-------|---------|
| `historical_outcomes` | Post-eval horizons |
| `model_versions` / `model_training_runs` | Offline learning bookkeeping |
| `app_settings` | Key/value settings |
| `setup_check_runs` | Setup wizard results |
| `data_retention_policies` | Retention rules |
