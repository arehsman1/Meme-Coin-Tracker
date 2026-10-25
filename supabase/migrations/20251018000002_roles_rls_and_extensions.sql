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
