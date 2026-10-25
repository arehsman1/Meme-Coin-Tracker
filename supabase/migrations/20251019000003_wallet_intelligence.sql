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
