# Security summary — Meme Coin Tracker

## Trust boundaries

1. **Browser / Mini App** — untrusted. Never receives service-role key, bot token, or provider secrets.
2. **Telegram Bot API** — trusted for `effective_user.id` on bot updates only.
3. **Telegram Web App `initData`** — verified with HMAC-SHA256 (`WebAppData` + bot token) before any privilege.
4. **Supabase service role** — server-only; bypasses RLS; used by API/worker.
5. **Supabase anon / authenticated** — constrained by RLS.

## Roles

| Role | Notes |
|------|--------|
| owner | Full settings + role assignment; first `TELEGRAM_ADMIN_IDS` entry treated as owner in API |
| admin | Scanner/providers/jobs; cannot assign owner or service_worker |
| analyst | Read evidence/evaluations/outcomes |
| user | Own watchlist/alerts + read tokens |
| service_worker | Never assignable via client; backend jobs only |
| read_only | Read dashboards |

## Critical rules enforced

- Client cannot self-promote to admin/owner/service_worker (RLS + API checks).
- `X-Telegram-User-Id` alone is **rejected** for admin routes.
- Trading / wallet signing cannot be set to ON via admin API.
- Qualification decisions written only by backend (service role).
- Notification outbox / job leases not readable by normal clients (RLS deny).
- Audit log on privileged scanner setting changes when Supabase is ready.

## Secrets

| Secret | Location |
|--------|----------|
| `SUPABASE_SERVICE_ROLE_KEY` | Server env only |
| `TELEGRAM_BOT_TOKEN` | Server env only |
| `TELEGRAM_ADMIN_IDS` | Server env (bootstrap allowlist) |
| Provider API keys | Server env only |

## Remaining hardening (owner action)

- Restrict CORS to Mini App origin in production.
- Seed `admin_allowlist` rows matching production Telegram IDs after migration.
- Rotate keys if ever exposed in logs or client bundles.


## Rate limiting & headers (API process)

- Sliding-window limiter: 120 req/min default, 30 req/min for write-ish paths
- Security headers: nosniff, DENY framing, no-referrer, no-store
- CORS: Mini App / APP_BASE_URL when set; `*` only in development
- In-memory limits are per process — put a reverse proxy limit in production as well
