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
