-- =============================================================================
-- Meme Coin Tracker — ALL migrations combined (apply once in Supabase SQL Editor)
-- Order: 000001 → 000002 → 000003 → 000004
-- Safe to re-run only if individual statements use IF NOT EXISTS / exception handlers
-- Trading remains OFF; this schema does not enable auto-trade.
-- =============================================================================

-- >>> FILE: 20251018000001_initial_schema.sql

-- Meme Coin Tracker - Initial schema
-- Additive, versioned migration. Safe for fresh installs.
-- Never drops production data.

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE chain_type AS ENUM ('solana', 'ethereum', 'base');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE check_result_type AS ENUM ('PASS', 'FAIL', 'UNKNOWN', 'NOT_APPLICABLE');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE qualification_status_type AS ENUM (
        'REJECTED_CRITICAL_RISK',
        'PENDING_DATA_INCOMPLETE',
        'MONITOR_NOT_YET_QUALIFIED',
        'SECURITY_QUALIFIED_NO_TRADE_SIGNAL',
        'QUALIFIED_TRADE_CANDIDATE',
        'QUALIFICATION_REVOKED'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE service_desired_state AS ENUM ('ON', 'OFF');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE service_actual_state AS ENUM (
        'RUNNING', 'PAUSED', 'STOPPED', 'DEGRADED', 'BLOCKED', 'STARTING', 'STOPPING'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE health_state AS ENUM ('HEALTHY', 'DEGRADED', 'UNHEALTHY', 'UNKNOWN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- App users & roles (extends Supabase auth.users)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.app_users (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    telegram_id BIGINT UNIQUE,
    display_name TEXT,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, role)
);

-- ---------------------------------------------------------------------------
-- Global & chain settings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.app_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID REFERENCES public.app_users(id)
);

CREATE TABLE IF NOT EXISTS public.scanner_service_settings (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
    continuous_scanning service_desired_state NOT NULL DEFAULT 'ON',
    automatic_scam_monitoring service_desired_state NOT NULL DEFAULT 'ON',
    historical_outcome_collection service_desired_state NOT NULL DEFAULT 'ON',
    telegram_qualification_alerts service_desired_state NOT NULL DEFAULT 'ON',
    telegram_risk_alerts service_desired_state NOT NULL DEFAULT 'ON',
    live_trading service_desired_state NOT NULL DEFAULT 'OFF',
    automatic_buying service_desired_state NOT NULL DEFAULT 'OFF',
    automatic_selling service_desired_state NOT NULL DEFAULT 'OFF',
    wallet_signing service_desired_state NOT NULL DEFAULT 'OFF',
    redis_dependency service_desired_state NOT NULL DEFAULT 'OFF',
    actual_state service_actual_state NOT NULL DEFAULT 'STOPPED',
    health_state health_state NOT NULL DEFAULT 'UNKNOWN',
    last_successful_scan TIMESTAMPTZ,
    next_scheduled_scan TIMESTAMPTZ,
    last_db_write TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.scanner_chain_settings (
    chain chain_type PRIMARY KEY,
    desired_state service_desired_state NOT NULL DEFAULT 'ON',
    actual_state service_actual_state NOT NULL DEFAULT 'STOPPED',
    health_state health_state NOT NULL DEFAULT 'UNKNOWN',
    last_successful_scan TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Provider registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.provider_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    chain chain_type,
    capabilities TEXT[] NOT NULL DEFAULT '{}',
    auth_required BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit_per_minute INT,
    request_timeout_ms INT NOT NULL DEFAULT 10000,
    health_state health_state NOT NULL DEFAULT 'UNKNOWN',
    last_successful_request TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, chain)
);

CREATE TABLE IF NOT EXISTS public.provider_health_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id UUID NOT NULL REFERENCES public.provider_registry(id) ON DELETE CASCADE,
    health_state health_state NOT NULL,
    message TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Tokens & observations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    address TEXT NOT NULL,
    name TEXT,
    symbol TEXT,
    decimals INT,
    creation_time TIMESTAMPTZ,
    first_trade_time TIMESTAMPTZ,
    metadata_source TEXT,
    current_status qualification_status_type,
    current_evaluation_id UUID,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, address)
);

CREATE INDEX IF NOT EXISTS idx_tokens_status ON public.tokens (current_status);
CREATE INDEX IF NOT EXISTS idx_tokens_discovered ON public.tokens (discovered_at DESC);

CREATE TABLE IF NOT EXISTS public.token_markets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    pool_address TEXT,
    dex TEXT,
    quote_symbol TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.token_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    observation_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    source TEXT NOT NULL,
    source_timestamp TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_observations_token ON public.token_observations (token_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS public.token_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    mint_authority TEXT,
    freeze_authority TEXT,
    owner TEXT,
    is_mintable BOOLEAN,
    is_freezable BOOLEAN,
    is_pausable BOOLEAN,
    has_blacklist BOOLEAN,
    has_transfer_hook BOOLEAN,
    is_proxy BOOLEAN,
    buy_tax_bps INT,
    sell_tax_bps INT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT
);

CREATE TABLE IF NOT EXISTS public.holder_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    top_holder_pct NUMERIC(8,4),
    top10_pct NUMERIC(8,4),
    creator_pct NUMERIC(8,4),
    unique_holders INT,
    excluded_addresses TEXT[],
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT
);

CREATE TABLE IF NOT EXISTS public.liquidity_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    pool_address TEXT,
    dex TEXT,
    base_reserve NUMERIC,
    quote_reserve NUMERIC,
    quote_symbol TEXT,
    liquidity_usd NUMERIC,
    locked BOOLEAN,
    lock_until TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT
);

CREATE TABLE IF NOT EXISTS public.sellability_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('VERIFIED', 'LIMITED', 'UNKNOWN', 'FAILED')),
    method TEXT,
    price_impact_pct NUMERIC,
    route_available BOOLEAN,
    limitations TEXT[],
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT
);

-- ---------------------------------------------------------------------------
-- Qualification
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.qualification_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version TEXT NOT NULL UNIQUE,
    description TEXT,
    config JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.qualification_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    revision INT NOT NULL DEFAULT 1,
    policy_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    final_status qualification_status_type NOT NULL,
    security_risk_score NUMERIC(5,1) NOT NULL,
    manipulation_risk_score NUMERIC(5,1) NOT NULL,
    trade_quality_score NUMERIC(5,1) NOT NULL,
    data_confidence_score NUMERIC(5,1) NOT NULL,
    reasons TEXT[] NOT NULL DEFAULT '{}',
    missing_evidence TEXT[] NOT NULL DEFAULT '{}',
    conflicting_evidence TEXT[] NOT NULL DEFAULT '{}',
    data_freshness JSONB,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (token_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_eval_token ON public.qualification_evaluations (token_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_status ON public.qualification_evaluations (final_status);

CREATE TABLE IF NOT EXISTS public.qualification_check_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID NOT NULL REFERENCES public.qualification_evaluations(id) ON DELETE CASCADE,
    check_id TEXT NOT NULL,
    name TEXT NOT NULL,
    result check_result_type NOT NULL,
    severity TEXT NOT NULL DEFAULT 'normal',
    reason TEXT,
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    evidence_refs TEXT[]
);

CREATE TABLE IF NOT EXISTS public.qualification_score_components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID NOT NULL REFERENCES public.qualification_evaluations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    weight NUMERIC,
    raw_value NUMERIC,
    normalized NUMERIC,
    contribution NUMERIC
);

CREATE TABLE IF NOT EXISTS public.qualification_status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    evaluation_id UUID REFERENCES public.qualification_evaluations(id),
    from_status qualification_status_type,
    to_status qualification_status_type NOT NULL,
    reason TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Watchlists, alerts, notifications
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.token_watchlists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, token_id)
);

CREATE TABLE IF NOT EXISTS public.alert_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (user_id, alert_type)
);

CREATE TABLE IF NOT EXISTS public.notification_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notification_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    recipient_telegram_id BIGINT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed', 'deduplicated')),
    dedupe_key TEXT,
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_dedupe
    ON public.notification_outbox (dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.notification_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    outbox_id UUID NOT NULL REFERENCES public.notification_outbox(id) ON DELETE CASCADE,
    telegram_message_id BIGINT,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    success BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS public.risk_alert_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    evaluation_id UUID REFERENCES public.qualification_evaluations(id),
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Scanner jobs & heartbeats
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.scanner_worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'alive',
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS public.scanner_job_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type TEXT NOT NULL,
    chain chain_type,
    token_id UUID REFERENCES public.tokens(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON public.scanner_job_runs (status, created_at);

CREATE TABLE IF NOT EXISTS public.scanner_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_key TEXT NOT NULL UNIQUE,
    interval_seconds INT NOT NULL,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

-- ---------------------------------------------------------------------------
-- Historical outcomes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.historical_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id UUID NOT NULL REFERENCES public.qualification_evaluations(id) ON DELETE CASCADE,
    horizon_minutes INT NOT NULL,
    price_change_pct NUMERIC,
    max_favorable_pct NUMERIC,
    max_adverse_pct NUMERIC,
    liquidity_change_pct NUMERIC,
    exit_impact_pct NUMERIC,
    route_still_available BOOLEAN,
    sell_failure_observed BOOLEAN,
    holder_concentration_change NUMERIC,
    qualification_revoked BOOLEAN,
    major_adverse_event BOOLEAN,
    outcome_status TEXT NOT NULL DEFAULT 'immature'
        CHECK (outcome_status IN ('complete', 'immature', 'unavailable', 'censored')),
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (evaluation_id, horizon_minutes)
);

CREATE TABLE IF NOT EXISTS public.model_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.model_training_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version_id UUID REFERENCES public.model_versions(id),
    metrics JSONB,
    dataset_split JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Audit
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id UUID REFERENCES public.app_users(id),
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON public.audit_logs (created_at DESC);

-- ---------------------------------------------------------------------------
-- Updated_at trigger helper
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    CREATE TRIGGER trg_tokens_updated BEFORE UPDATE ON public.tokens
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_app_users_updated BEFORE UPDATE ON public.app_users
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- Seed default scanner settings (idempotent)
-- ---------------------------------------------------------------------------
INSERT INTO public.scanner_service_settings (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.scanner_chain_settings (chain, desired_state)
VALUES
    ('solana', 'ON'),
    ('ethereum', 'ON'),
    ('base', 'ON')
ON CONFLICT (chain) DO NOTHING;

INSERT INTO public.qualification_policies (version, description, is_active, config)
VALUES (
    '0.1.0',
    'Initial deterministic policy',
    TRUE,
    '{"min_confidence_for_qualify": 55, "max_security_risk": 40, "max_manipulation_risk": 55, "min_trade_quality": 45}'::jsonb
)
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------------
-- RLS (basic; tighten per deployment)
-- ---------------------------------------------------------------------------
ALTER TABLE public.app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.token_watchlists ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alert_preferences ENABLE ROW LEVEL SECURITY;

-- Users can read/update their own profile
CREATE POLICY app_users_self ON public.app_users
    FOR ALL USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- Watchlist owned by user
CREATE POLICY watchlist_owner ON public.token_watchlists
    FOR ALL USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY alert_prefs_owner ON public.alert_preferences
    FOR ALL USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Service-role / backend bypasses RLS; public read of tokens is intentional for Mini App
ALTER TABLE public.tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY tokens_public_read ON public.tokens FOR SELECT USING (true);

ALTER TABLE public.qualification_evaluations ENABLE ROW LEVEL SECURITY;
CREATE POLICY evals_public_read ON public.qualification_evaluations FOR SELECT USING (true);

-- >>> FILE: 20251018000002_roles_rls_and_extensions.sql

-- Meme Coin Tracker — Additive migration: roles, RLS, missing entities
-- Safe for existing installs. Does NOT drop data or reset settings.
-- Expand-only: new tables, altered constraints, new policies.

-- ---------------------------------------------------------------------------
-- Application role enum (logical roles)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE app_role_type AS ENUM (
        'owner',
        'admin',
        'analyst',
        'user',
        'service_worker',
        'read_only'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- Extend app_users (telegram link already present)
-- ---------------------------------------------------------------------------
ALTER TABLE public.app_users
    ADD COLUMN IF NOT EXISTS auth_provider TEXT DEFAULT 'telegram',
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- Replace narrow user_roles check with expanded roles via new constraint path
-- Keep existing rows; add new allowed values by dropping old CHECK if present
-- and adding expanded one. Idempotent pattern.
-- ---------------------------------------------------------------------------
ALTER TABLE public.user_roles DROP CONSTRAINT IF EXISTS user_roles_role_check;
ALTER TABLE public.user_roles
    ADD CONSTRAINT user_roles_role_check
    CHECK (role IN ('owner', 'admin', 'analyst', 'user', 'service_worker', 'read_only', 'operator', 'viewer'));
-- Map legacy: operator≈admin, viewer≈read_only (application layer)

-- ---------------------------------------------------------------------------
-- role_permissions — explicit capability matrix
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.role_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role TEXT NOT NULL CHECK (role IN (
        'owner', 'admin', 'analyst', 'user', 'service_worker', 'read_only'
    )),
    permission TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (role, permission)
);

-- ---------------------------------------------------------------------------
-- admin_allowlist — Telegram IDs authorized as owner/admin (server-seeded)
-- Never trust client-supplied telegram ids for privilege elevation.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.admin_allowlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id BIGINT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin')),
    label TEXT,
    created_by UUID REFERENCES public.app_users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_admin_allowlist_tg
    ON public.admin_allowlist (telegram_id)
    WHERE is_active = TRUE AND revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- chain_registry (explicit, in addition to enum)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.chain_registry (
    chain chain_type PRIMARY KEY,
    display_name TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    explorer_url_template TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.chain_registry (chain, display_name, explorer_url_template)
VALUES
    ('solana', 'Solana Mainnet', 'https://solscan.io/token/{address}'),
    ('ethereum', 'Ethereum Mainnet', 'https://etherscan.io/token/{address}'),
    ('base', 'Base Mainnet', 'https://basescan.org/token/{address}')
ON CONFLICT (chain) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Additional evidence / risk tables
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.wallet_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    address TEXT NOT NULL,
    label TEXT,
    entity_type TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}',
    UNIQUE (chain, address)
);

CREATE TABLE IF NOT EXISTS public.wallet_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    wallet_a TEXT NOT NULL,
    wallet_b TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (confidence IN ('CONFIRMED', 'PROBABLE', 'POSSIBLE', 'UNKNOWN')),
    evidence JSONB NOT NULL DEFAULT '[]',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallet_rel_a ON public.wallet_relationships (chain, wallet_a);
CREATE INDEX IF NOT EXISTS idx_wallet_rel_b ON public.wallet_relationships (chain, wallet_b);

CREATE TABLE IF NOT EXISTS public.creator_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    creator_address TEXT NOT NULL,
    token_id UUID REFERENCES public.tokens(id) ON DELETE SET NULL,
    summary JSONB NOT NULL DEFAULT '{}',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.manipulation_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    signal_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'normal',
    confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    details JSONB NOT NULL DEFAULT '{}',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_manip_token ON public.manipulation_signals (token_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS public.market_activity_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id UUID NOT NULL REFERENCES public.tokens(id) ON DELETE CASCADE,
    window_seconds INT NOT NULL,
    buy_count INT,
    sell_count INT,
    unique_buyers INT,
    unique_sellers INT,
    volume_usd NUMERIC,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT
);

CREATE TABLE IF NOT EXISTS public.service_state_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.setup_check_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    check_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass', 'fail', 'warn', 'skip')),
    details JSONB NOT NULL DEFAULT '{}',
    ran_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.data_retention_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resource TEXT NOT NULL UNIQUE,
    retain_days INT NOT NULL CHECK (retain_days > 0),
    description TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.data_retention_policies (resource, retain_days, description)
VALUES
    ('token_observations', 30, 'Raw observation payloads'),
    ('provider_health_events', 14, 'Provider health events'),
    ('notification_outbox', 90, 'Notification outbox rows'),
    ('audit_logs', 365, 'Security audit trail')
ON CONFLICT (resource) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Seed role_permissions (idempotent)
-- ---------------------------------------------------------------------------
INSERT INTO public.role_permissions (role, permission, description) VALUES
    ('owner', 'settings.global.write', 'Change global app and scanner settings'),
    ('owner', 'roles.assign', 'Assign any role including owner transfer'),
    ('owner', 'policies.write', 'Edit qualification policies'),
    ('owner', 'providers.write', 'Configure providers'),
    ('owner', 'audit.read', 'Read audit logs'),
    ('admin', 'settings.scanner.write', 'Change scanner desired state'),
    ('admin', 'providers.write', 'Configure providers'),
    ('admin', 'jobs.manage', 'Pause/resume/safe-stop workers'),
    ('admin', 'audit.read', 'Read audit logs'),
    ('admin', 'tokens.evaluate', 'Trigger evaluations'),
    ('analyst', 'tokens.read', 'Read tokens and evaluations'),
    ('analyst', 'evidence.read', 'Read evidence and outcomes'),
    ('analyst', 'analytics.read', 'Read historical analytics'),
    ('user', 'tokens.read', 'Read permitted token reports'),
    ('user', 'watchlist.manage', 'Manage own watchlist'),
    ('user', 'alerts.manage', 'Manage own alert preferences'),
    ('service_worker', 'jobs.claim', 'Claim and complete scanner jobs'),
    ('service_worker', 'tokens.write', 'Persist evaluations and evidence'),
    ('service_worker', 'notifications.send', 'Process notification outbox'),
    ('service_worker', 'heartbeats.write', 'Write worker heartbeats'),
    ('read_only', 'tokens.read', 'Read permitted dashboards'),
    ('read_only', 'analytics.read', 'Read reports')
ON CONFLICT (role, permission) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Helper: current app role checks (SECURITY DEFINER, locked search_path)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.has_app_role(required_role TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    uid UUID;
BEGIN
    uid := auth.uid();
    IF uid IS NULL THEN
        RETURN FALSE;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.user_roles ur
        WHERE ur.user_id = uid AND ur.role = required_role
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.has_any_app_role(required_roles TEXT[])
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    uid UUID;
BEGIN
    uid := auth.uid();
    IF uid IS NULL THEN
        RETURN FALSE;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM public.user_roles ur
        WHERE ur.user_id = uid AND ur.role = ANY(required_roles)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.is_telegram_admin(tg_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM public.admin_allowlist a
        WHERE a.telegram_id = tg_id
          AND a.is_active = TRUE
          AND a.revoked_at IS NULL
    );
END;
$$;

REVOKE ALL ON FUNCTION public.has_app_role(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.has_any_app_role(TEXT[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.is_telegram_admin(BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.has_app_role(TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION public.has_any_app_role(TEXT[]) TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_telegram_admin(BIGINT) TO authenticated;

-- ---------------------------------------------------------------------------
-- RLS: enable on sensitive tables
-- ---------------------------------------------------------------------------
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admin_allowlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scanner_service_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scanner_chain_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.qualification_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scanner_job_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scanner_worker_heartbeats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_settings ENABLE ROW LEVEL SECURITY;

-- Drop overly broad policies if we need to replace (idempotent names)
DROP POLICY IF EXISTS user_roles_self_read ON public.user_roles;
DROP POLICY IF EXISTS user_roles_admin_write ON public.user_roles;
DROP POLICY IF EXISTS role_permissions_read ON public.role_permissions;
DROP POLICY IF EXISTS admin_allowlist_admin_read ON public.admin_allowlist;
DROP POLICY IF EXISTS audit_logs_admin_read ON public.audit_logs;
DROP POLICY IF EXISTS scanner_settings_admin ON public.scanner_service_settings;
DROP POLICY IF EXISTS scanner_chain_admin ON public.scanner_chain_settings;
DROP POLICY IF EXISTS policies_admin ON public.qualification_policies;
DROP POLICY IF EXISTS providers_admin ON public.provider_registry;
DROP POLICY IF EXISTS outbox_deny_client ON public.notification_outbox;
DROP POLICY IF EXISTS jobs_deny_client ON public.scanner_job_runs;
DROP POLICY IF EXISTS heartbeats_deny_client ON public.scanner_worker_heartbeats;
DROP POLICY IF EXISTS app_settings_admin ON public.app_settings;

-- Users can read their own roles
CREATE POLICY user_roles_self_read ON public.user_roles
    FOR SELECT USING (auth.uid() = user_id);

-- Only owner/admin (via has_app_role) may insert roles — still blocked for self-promotion to owner
CREATE POLICY user_roles_admin_write ON public.user_roles
    FOR ALL USING (
        public.has_any_app_role(ARRAY['owner', 'admin'])
    )
    WITH CHECK (
        public.has_any_app_role(ARRAY['owner', 'admin'])
        AND (
            role <> 'owner'
            OR public.has_app_role('owner')
        )
        AND role <> 'service_worker'  -- service_worker never assigned via client
    );

CREATE POLICY role_permissions_read ON public.role_permissions
    FOR SELECT USING (auth.uid() IS NOT NULL);

CREATE POLICY admin_allowlist_admin_read ON public.admin_allowlist
    FOR SELECT USING (public.has_any_app_role(ARRAY['owner', 'admin']));

-- No client INSERT/UPDATE on admin_allowlist (service role only)
-- (absence of write policy + RLS = deny for authenticated clients)

CREATE POLICY audit_logs_admin_read ON public.audit_logs
    FOR SELECT USING (public.has_any_app_role(ARRAY['owner', 'admin']));

CREATE POLICY scanner_settings_admin ON public.scanner_service_settings
    FOR SELECT USING (true);  -- desired state visible
-- writes: no policy for authenticated → service role only

CREATE POLICY scanner_chain_admin ON public.scanner_chain_settings
    FOR SELECT USING (true);

CREATE POLICY policies_admin ON public.qualification_policies
    FOR SELECT USING (true);

CREATE POLICY providers_admin ON public.provider_registry
    FOR SELECT USING (true);

-- Outbox / jobs / heartbeats: no client access (service role bypasses RLS)
CREATE POLICY outbox_deny_client ON public.notification_outbox
    FOR SELECT USING (false);

CREATE POLICY jobs_deny_client ON public.scanner_job_runs
    FOR SELECT USING (false);

CREATE POLICY heartbeats_deny_client ON public.scanner_worker_heartbeats
    FOR SELECT USING (false);

CREATE POLICY app_settings_admin ON public.app_settings
    FOR SELECT USING (public.has_any_app_role(ARRAY['owner', 'admin']));

-- Watchlists already owner-scoped in migration 1

-- ---------------------------------------------------------------------------
-- Grants: authenticated can use tables according to RLS; service_role full
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO authenticated, anon;
GRANT SELECT ON public.tokens TO authenticated, anon;
GRANT SELECT ON public.qualification_evaluations TO authenticated, anon;
GRANT SELECT ON public.role_permissions TO authenticated;
GRANT SELECT ON public.chain_registry TO authenticated, anon;
GRANT SELECT ON public.scanner_service_settings TO authenticated;
GRANT SELECT ON public.scanner_chain_settings TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.token_watchlists TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.alert_preferences TO authenticated;
GRANT SELECT ON public.app_users TO authenticated;
GRANT SELECT ON public.user_roles TO authenticated;

-- service_role used by backend already has bypass RLS by design in Supabase

-- ---------------------------------------------------------------------------
-- Ensure scanner defaults exist without overwriting admin changes
-- ---------------------------------------------------------------------------
INSERT INTO public.scanner_service_settings (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.scanner_chain_settings (chain, desired_state)
VALUES
    ('solana', 'ON'),
    ('ethereum', 'ON'),
    ('base', 'ON')
ON CONFLICT (chain) DO NOTHING;

-- >>> FILE: 20251019000003_wallet_intelligence.sql

-- Phase 4 — Wallet Intelligence foundation
-- Public wallet tracking is independent of personal wallet connection.
-- All trading / signing columns remain OFF by default elsewhere.

CREATE TABLE IF NOT EXISTS public.tracked_wallets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    address TEXT NOT NULL,
    label TEXT,
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'creator', 'early_buyer', 'significant_buyer',
                          'significant_seller', 'discovered', 'rule', 'import')),
    tags TEXT[] NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT true,
    significant_swap_usd_threshold NUMERIC(18, 2),  -- NULL = use global default
    notes TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, address)
);

CREATE INDEX IF NOT EXISTS idx_tracked_wallets_active
    ON public.tracked_wallets (is_active, chain) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_tracked_wallets_last_activity
    ON public.tracked_wallets (last_activity_at DESC NULLS LAST);

-- Canonical transaction / event log for tracked wallets
CREATE TABLE IF NOT EXISTS public.wallet_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    wallet_address TEXT NOT NULL,
    tx_signature TEXT NOT NULL,          -- Solana signature or EVM tx hash
    log_index INT,                       -- EVM log index when applicable
    block_time TIMESTAMPTZ,
    event_type TEXT NOT NULL
        CHECK (event_type IN (
            'SWAP_BUY', 'SWAP_SELL', 'TRANSFER_IN', 'TRANSFER_OUT',
            'LIQUIDITY_ADD', 'LIQUIDITY_REMOVE', 'CONTRACT_INTERACTION',
            'FAILED', 'UNKNOWN'
        )),
    token_address TEXT,
    token_id UUID REFERENCES public.tokens(id) ON DELETE SET NULL,
    amount_raw TEXT,                     -- keep exact on-chain amount as string
    amount_ui NUMERIC(36, 18),
    value_usd NUMERIC(24, 8),            -- NULL when unknown
    value_usd_status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (value_usd_status IN ('KNOWN', 'UNKNOWN', 'ESTIMATED')),
    counterparty TEXT,
    dex_or_protocol TEXT,
    is_significant BOOLEAN NOT NULL DEFAULT false,
    raw_provider_payload JSONB NOT NULL DEFAULT '{}',
    provider TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Expression unique index (log_index may be null)
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_tx_dedupe
    ON public.wallet_transactions (
        chain,
        tx_signature,
        wallet_address,
        (COALESCE(log_index, -1))
    );

CREATE INDEX IF NOT EXISTS idx_wallet_tx_wallet
    ON public.wallet_transactions (chain, wallet_address, block_time DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_wallet_tx_token
    ON public.wallet_transactions (token_id, block_time DESC NULLS LAST)
    WHERE token_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wallet_tx_significant
    ON public.wallet_transactions (is_significant, block_time DESC)
    WHERE is_significant = true;
CREATE INDEX IF NOT EXISTS idx_wallet_tx_type
    ON public.wallet_transactions (event_type, chain);

-- Lightweight profile / summary (updated by workers)
CREATE TABLE IF NOT EXISTS public.wallet_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    address TEXT NOT NULL,
    display_name TEXT,
    classification TEXT[] NOT NULL DEFAULT '{}',
    observed_trade_count INT NOT NULL DEFAULT 0,
    observed_significant_count INT NOT NULL DEFAULT 0,
    first_observed_at TIMESTAMPTZ,
    last_observed_at TIMESTAMPTZ,
    data_completeness_score INT NOT NULL DEFAULT 0
        CHECK (data_completeness_score BETWEEN 0 AND 100),
    confidence_score INT NOT NULL DEFAULT 0
        CHECK (confidence_score BETWEEN 0 AND 100),
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, address)
);

-- Global + per-user wallet alert preferences (minimal for Phase 4)
CREATE TABLE IF NOT EXISTS public.wallet_alert_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope TEXT NOT NULL DEFAULT 'global'
        CHECK (scope IN ('global', 'user')),
    user_id UUID,                        -- NULL for global
    significant_swap_usd NUMERIC(18, 2) NOT NULL DEFAULT 500.00,
    alert_on_buy BOOLEAN NOT NULL DEFAULT true,
    alert_on_sell BOOLEAN NOT NULL DEFAULT true,
    alert_on_new_token_entry BOOLEAN NOT NULL DEFAULT true,
    alert_on_coordinated BOOLEAN NOT NULL DEFAULT true,
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ensure a single global row
INSERT INTO public.wallet_alert_settings (scope, significant_swap_usd)
VALUES ('global', 500.00)
ON CONFLICT DO NOTHING;

-- RLS (service role bypasses; authenticated users see only what policy allows)
ALTER TABLE public.tracked_wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wallet_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wallet_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wallet_alert_settings ENABLE ROW LEVEL SECURITY;

-- Service role / owner full access patterns are handled via service-role key;
-- additional policies can be tightened later.
COMMENT ON TABLE public.tracked_wallets IS
    'Public wallets being monitored. Independent of personal wallet_connection_enabled.';
COMMENT ON TABLE public.wallet_transactions IS
    'Deduplicated on-chain activity for tracked wallets. SWAP_* vs TRANSFER_* is explicit.';

-- >>> FILE: 20251019000004_wallet_performance.sql

-- Phase 5 — Wallet performance ledger, snapshots, score components
-- Does not claim complete lifetime P&L when history is incomplete.

CREATE TABLE IF NOT EXISTS public.wallet_position_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    wallet_address TEXT NOT NULL,
    token_address TEXT NOT NULL,
    token_id UUID REFERENCES public.tokens(id) ON DELETE SET NULL,
    -- positive qty = long exposure in token units (UI amount)
    qty_open NUMERIC(36, 18) NOT NULL DEFAULT 0,
    cost_basis_usd NUMERIC(24, 8),           -- NULL if unknown
    cost_basis_status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (cost_basis_status IN ('KNOWN', 'UNKNOWN', 'ESTIMATED', 'MIXED')),
    realized_pnl_usd NUMERIC(24, 8) NOT NULL DEFAULT 0,
    realized_pnl_status TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (realized_pnl_status IN ('KNOWN', 'UNKNOWN', 'ESTIMATED', 'PARTIAL')),
    total_bought_qty NUMERIC(36, 18) NOT NULL DEFAULT 0,
    total_sold_qty NUMERIC(36, 18) NOT NULL DEFAULT 0,
    buy_count INT NOT NULL DEFAULT 0,
    sell_count INT NOT NULL DEFAULT 0,
    first_entry_at TIMESTAMPTZ,
    last_exit_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ,
    observation_complete BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, wallet_address, token_address)
);

CREATE INDEX IF NOT EXISTS idx_wpl_wallet
    ON public.wallet_position_ledger (chain, wallet_address);

CREATE TABLE IF NOT EXISTS public.wallet_performance_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    wallet_address TEXT NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    observed_trade_count INT NOT NULL DEFAULT 0,
    closed_trade_count INT NOT NULL DEFAULT 0,
    win_count INT NOT NULL DEFAULT 0,
    loss_count INT NOT NULL DEFAULT 0,
    -- win_rate only defined when closed_trade_count > 0
    win_rate NUMERIC(8, 4),
    realized_pnl_usd NUMERIC(24, 8),
    realized_pnl_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    unrealized_pnl_usd NUMERIC(24, 8),
    unrealized_pnl_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    sample_size INT NOT NULL DEFAULT 0,
    data_completeness_score INT NOT NULL DEFAULT 0
        CHECK (data_completeness_score BETWEEN 0 AND 100),
    confidence_score INT NOT NULL DEFAULT 0
        CHECK (confidence_score BETWEEN 0 AND 100),
    observation_period_start TIMESTAMPTZ,
    observation_period_end TIMESTAMPTZ,
    limitations JSONB NOT NULL DEFAULT '[]',
    scoring_rule_version TEXT NOT NULL DEFAULT 'v0.1',
    components JSONB NOT NULL DEFAULT '{}',
    UNIQUE (chain, wallet_address, snapshot_at)
);

CREATE INDEX IF NOT EXISTS idx_wps_wallet
    ON public.wallet_performance_snapshots (chain, wallet_address, snapshot_at DESC);

CREATE TABLE IF NOT EXISTS public.wallet_score_components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chain chain_type NOT NULL,
    wallet_address TEXT NOT NULL,
    rule_version TEXT NOT NULL DEFAULT 'v0.1',
    component_key TEXT NOT NULL,
    component_value NUMERIC(18, 6),
    weight NUMERIC(8, 4),
    notes TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, wallet_address, rule_version, component_key)
);

-- Expand relationships if needed (table already exists from migration 2)
COMMENT ON TABLE public.wallet_position_ledger IS
    'Per-wallet per-token open qty and realized PnL from observed swaps only. Transfers do not open cost basis.';
COMMENT ON TABLE public.wallet_performance_snapshots IS
    'Point-in-time performance with explicit sample size and completeness. Not a guarantee of future returns.';

-- =============================================================================
-- END combined migrations
-- =============================================================================
