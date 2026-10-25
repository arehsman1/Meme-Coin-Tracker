# IMPLEMENTATION_PLAN.md — Meme Coin Tracker

**Last updated:** 2026-10-19  
**Status legend:** `COMPLETE` | `IN_PROGRESS` | `PARTIAL` | `NOT_STARTED` | `BLOCKED` (credentials / external)

This document is the living project plan. It maps the master specification (14 phases + supplementary requirements) against the actual repository state. No claim of completeness is made for any phase that has not been tested end-to-end with real credentials.

---

## 1. Architecture summary (authoritative)

| Layer | Technology | Responsibility |
|-------|------------|----------------|
| Discovery / high-throughput ingestion | Rust (`meme_common`, `meme_chains`, `meme_scanner`) | DexScreener polling, RPC probes, event normalisation, deduplication, enqueue evaluate jobs |
| Authoritative backend | Python 3.12 + FastAPI + Pydantic v2 | Qualification engine, Wallet Intelligence, settings, auth, outbox, workers, API |
| Persistence | Supabase PostgreSQL | All durable state, RLS, versioned migrations |
| Cache / queue (optional) | Redis | Disabled by default; not required for correctness |
| Notifications | python-telegram-bot + persistent outbox | Token + wallet alerts, commands |
| Mini App | React + Vite + Telegram Mini App SDK | Dashboard, token/wallet investigation, settings |
| Trading | Explicitly OFF | Paper / live / signing all default OFF and gated |

**Single source of truth for qualification:** Python `qualification.engine`.  
**Rust never makes qualification decisions.**

---

## 2. Engineering decision report (required by supplementary master requirements)

### 2.1 Recommended providers (by capability)

| Capability | Solana primary | Solana fallback | Ethereum primary | Base primary | Notes |
|------------|----------------|-----------------|------------------|--------------|-------|
| RPC / reads | Helius | Public RPC / Triton | Alchemy | Alchemy (same key) | Helius free = 1M credits/mo, 10 rps |
| New token / pool discovery | DexScreener (poll) | Helius webhooks / LaserStream | DexScreener | DexScreener | DexScreener free, 60–300 req/min by endpoint |
| Historical tx / wallet history | Helius (DAS + enhanced) | — | Alchemy Transfers / Token API | Alchemy | Limited free depth |
| Token metadata / holders | Helius DAS | — | Alchemy Token API | Alchemy | |
| Market data / liquidity | DexScreener | Birdeye (optional paid) | DexScreener | DexScreener | |
| Security / contract analysis | Custom heuristics + Helius | GoPlus / token-sniffer style (future) | Custom + Alchemy | Custom | Never treat “verified” as safe |
| Swap simulation / execution | Jupiter (later) | — | 0x / Uniswap router | 0x / Aerodrome | Live execution remains OFF |
| Streaming | Helius LaserStream (paid) | — | Alchemy websockets | Alchemy | Optional; poll first |

**Selection rationale (weights applied):** data correctness 30 %, reliability 20 %, historical 15 %, cost 15 %, rate limits 10 %, integration complexity 10 %.  
Helius + Alchemy + DexScreener give the best cost/coverage trade-off for a small-production profile. All providers are behind adapters; qualification never depends on a single vendor.

### 2.2 Current verified pricing / quotas (as of 2026-10)

- **Helius** (Solana): Free $0 / 1 M credits / 10 rps; Developer $49 / 10 M credits / 50 rps. Extra credits ~$5 / M.  
  Source: https://www.helius.dev/docs/billing/plans
- **Alchemy** (Ethereum + Base): Free 30 M CU / ~15–25 rps; Pay-as-you-go from ~$0.40–0.525 / M CU.  
  Source: https://www.alchemy.com/pricing
- **DexScreener**: Public free API, no key. 300 req/min (pairs/tokens), 60 req/min (profiles/boosts). No published paid self-serve plan.  
  Source: official API reference + community measurements.

**Assumption label:** prices are taken from published pages on 2026-10-16/19; they can change. Budget controls will treat free-tier exhaustion as “UNKNOWN evidence”, not silent PASS.

### 2.3 Workload assumptions (initial conservative profile)

| Metric | Assumption (Profile B – small production) |
|--------|-------------------------------------------|
| Chains | 3 (Solana, Ethereum, Base) |
| New pairs discovered / hour | ≤ 200 (DexScreener poll) |
| Tokens evaluated / day | ≤ 1 000 detailed |
| Tracked public wallets | ≤ 500 active |
| Significant swaps processed / day | ≤ 5 000 |
| Concurrent Mini App users | ≤ 20 |
| Alerts / day | ≤ 50 |
| Historical retention | 90 days raw evidence, 1 year outcomes |
| DB growth | < 5 GB / month under above load |

These are **assumptions**, not guarantees. Load tests will validate before any “supported” claim.

### 2.4 Estimated monthly cost (Profile B)

| Item | Estimate | Notes |
|------|----------|-------|
| Helius Developer | $49 | Needed for reliable Solana wallet history |
| Alchemy free → light PAYG | $0–30 | Free tier usually sufficient at start |
| DexScreener | $0 | Free |
| Supabase Pro (or free) | $0–25 | Free tier for early; Pro when storage grows |
| Hosting (single VPS / Railway / Fly) | $10–40 | API + worker + Mini App static |
| Telegram | $0 | |
| **Total** | **~$60–150** | Strict budget caps will pause non-critical enrichment |

Profile A (dev) = $0 (mocks + free tiers).  
Profile C (expanded) would move Helius to Business ($499) and add paid market-data.

### 2.5 Performance targets (initial)

| Stage | Target (p95) | Measurement |
|-------|--------------|-------------|
| Discovery → enqueue | < 30 s | Scanner timestamps |
| Enqueue → qualification decision | < 120 s | Job + evaluation timestamps |
| Qualification → outbox | < 5 s | |
| API read (token list) | < 300 ms | |
| Wallet activity ingest | ≥ 50 events / s (burst) | |
| Recovery after restart | No lost jobs; resume from checkpoint | |

### 2.6 Chain-specific execution & signing (trading remains OFF)

- **Solana:** Jupiter quote + versioned transaction; recent-blockhash expiry; priority fees; ATA handling; never store private keys.  
- **Ethereum / Base:** 0x / Uniswap v3 router, nonce management, EIP-1559, approval checks, reorg depth ≥ 12 (ETH) / 5 (Base).  
- Signing: external wallet (WalletConnect / Phantom) for manual; dedicated isolated signer service only after formal security review. Seed phrases never accepted in UI or Telegram.

### 2.7 Security review plan for live trading

1. Threat model (already partially in `docs/SECURITY_SUMMARY.md`).  
2. Key isolation, least-privilege, audit trail for every signature request.  
3. Pre-trade validation matrix (qualification + fresh liquidity + limits).  
4. Emergency stop that blocks new orders and surfaces pending tx status.  
5. Independent security review before any live-trading flag can be flipped.  
**Current state:** all trading flags default OFF and are enforced in code.

### 2.8 Provisional qualification thresholds (v0.1 – labelled provisional)

| Gate | Provisional value | Rationale / limitation |
|------|-------------------|------------------------|
| Min liquidity (USD) | $5 000 | Conservative; calibrated later with historical data |
| Max sell slippage (est.) | 8 % | On documented trade size |
| Max holder concentration (top-10) | 40 % | When reliable holder data exists |
| Min data_confidence_score | 60 | Blocks when critical evidence UNKNOWN |
| Max evidence age | 15 min (liquidity), 1 h (permissions) | Chain-aware |
| Significant swap threshold | $500 USD | Configurable |

All thresholds live in versioned `qualification_policies`. They are **hypotheses**, not proven profitable.

### 2.9 Historical validation plan

- Replay discovery + qualification on historical DexScreener / RPC snapshots with no look-ahead.  
- Metrics: qualification rate, revocation rate, false-positive risk labels, simulated exit feasibility, sample size, data completeness.  
- Walk-forward: calibrate on period N, validate on N+1.  
- Never claim profitability from peak-price multiples alone.

### 2.10 Phase sequence & acceptance (summary)

See section 3. Each phase ends only when code + tests + docs match acceptance criteria and secrets remain unexposed.

### 2.11 Known risks / unresolved decisions

- Live Supabase project + service-role key still owner-controlled.  
- Mini App public HTTPS URL + BotFather domain still required.  
- Sandbox cannot run `cargo` build-scripts; Rust binary must be built on host.  
- Full holder / funding-flow graph analysis needs deeper historical providers (cost).  
- Automated signing architecture deferred until security review.

### 2.12 First concrete implementation increment (this session)

**Wallet Intelligence foundation (Phase 4)** — canonical wallet identity, manual tracking, transaction classification, significant-swap threshold, profiles, and basic API surface. Public tracking works without personal wallet connection.

---

## 3. Phase status against master specification

| Phase | Title | Status | Notes |
|-------|-------|--------|-------|
| 0 | Project foundation | COMPLETE | Repo, Rust workspace, Python package, Mini App scaffold, Docker Compose, .env.example, tests, CI skeleton |
| 1 | Database, identity, permissions | **NEAR COMPLETE** | Migrations 000001–000004 + RLS + roles; apply on live Supabase still owner-blocked |
| 2 | Configuration & setup wizard | **NEAR COMPLETE** | Public settings safety surface + Mini App Settings panel; full setup wizard optional |
| 3 | Blockchain providers & token discovery | **NEAR COMPLETE** | DexScreener discovery + pure extract + provider posture; live RPC keys still owner |
| 4 | Wallet Intelligence foundation | **COMPLETE** (eng) | Migration, classification, repo, API, alerts, Mini App screens |
| 5 | Wallet performance & relationships | **NEAR COMPLETE** | Ledger, scoring, POSSIBLE co-buy, performance API, Mini App panel, worker snapshots |
| 6 | Token security / liquidity / manipulation | **NEAR COMPLETE** | Sellability + structured manipulation risk module + engine wiring |
| 7 | Authoritative qualification engine | COMPLETE (core) | Deterministic, versioned, tested, revocation working |
| 8 | Background jobs & scheduling | **NEAR COMPLETE** | Discovery, outcomes, revocation, outbox, wallet perf loop, failure backoff |
| 9 | Telegram bot & notifications | **NEAR COMPLETE** | Wallet/outcomes commands + formatters; needs live bot token |
| 10 | Telegram Mini App | **NEAR COMPLETE** | Tokens, wallets, performance, settings safety, service monitoring |
| 11 | Optional wallet connection & trading | **GATES COMPLETE (OFF)** | TRADING_FEATURE_ENABLED=False; admin reject_on_flags; no execution path |
| 12 | Historical outcomes & analytics | **NEAR COMPLETE** | Reference price, multiples, summarize; live DexScreener collect needs network |
| 13 | Security, failure recovery, E2E | **NEAR COMPLETE** | Rate limits, headers, CORS, offline E2E smoke + CI; live E2E owner-blocked |
| 14 | Deployment & operational verification | **PARTIAL** | Compose + DEPLOYMENT.md + verify_deploy.sh; live verify owner-blocked |

---

## 4. Definition of done (per phase)

A phase is COMPLETE only when:

1. Required code exists and is reachable.  
2. Acceptance criteria from the master spec are met.  
3. Relevant automated tests have been executed and passed.  
4. Migrations / config / docs updated.  
5. Secrets are not present in the repository.  
6. Known limitations are recorded.  
7. Next phase can build on the work without rewriting core contracts.

---

## 5. Next actions (ordered)

1. Complete Phase 4 — Wallet Intelligence foundation (models, repository, classification, API, tests).  
2. Expand schema for full wallet ledger / performance if not already present.  
3. Wire wallet activity alerts into outbox (independent of token qualification).  
4. Mini App wallet screens.  
5. Continue provider hardening and historical outcome collection.  
6. Owner supplies live credentials → end-to-end verification.

Trading, live signing, and real-money execution remain permanently OFF until an explicit later security-authorised release.
