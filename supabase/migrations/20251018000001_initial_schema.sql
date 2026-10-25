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
