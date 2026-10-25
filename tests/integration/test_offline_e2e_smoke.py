"""
Offline end-to-end smoke (no live RPC, Supabase, or Telegram).

Walks the core pipeline with in-memory stores:
  track wallet → ingest swaps → performance → outcomes schedule →
  qualification engine on synthetic evidence → public safety flags.

Does not claim production readiness. Live credentials remain owner-blocked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meme_tracker.models.enums import (
    Chain,
    CheckResult,
    ValueUsdStatus,
    WalletEventType,
    WalletSource,
)
from meme_tracker.models.evidence import (
    CheckOutcome,
    EvidenceItem,
    HolderSnapshot,
    LiquiditySnapshot,
    SellabilityEvidence,
    TokenIdentity,
)
from meme_tracker.models.enums import SellabilityStatus
from meme_tracker.outcomes.collector import (
    apply_market_snapshot,
    list_outcomes,
    reset_outcomes,
    schedule_outcome_jobs,
    summarize_outcomes,
)
from meme_tracker.qualification.engine import create_engine
from meme_tracker.settings_public import build_public_settings
from meme_tracker.wallets.ledger import compute_performance
from meme_tracker.wallets.models import TrackedWalletCreate, WalletTransactionIn
from meme_tracker.wallets.repository import (
    ingest_wallet_transaction,
    list_tracked_wallets,
    list_wallet_transactions,
    upsert_tracked_wallet,
)
from meme_tracker.worker.wallet_jobs import (
    clear_snapshot_cache,
    get_latest_snapshot,
    run_wallet_performance_cycle,
)


def test_offline_e2e_wallet_to_performance_to_outcomes():
    reset_outcomes()
    clear_snapshot_cache()

    # 1. Track public wallet (no personal wallet)
    w = upsert_tracked_wallet(
        TrackedWalletCreate(
            chain=Chain.SOLANA,
            address="E2EWallet1111111111111111111111111111",
            label="e2e",
            source=WalletSource.MANUAL,
        )
    )
    assert w.is_active
    assert any(
        x.address == "E2EWallet1111111111111111111111111111"
        for x in list_tracked_wallets(active_only=True)
    )

    # 2. Ingest buy + sell with known USD
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    ingest_wallet_transaction(
        WalletTransactionIn(
            chain=Chain.SOLANA,
            wallet_address="E2EWallet1111111111111111111111111111",
            tx_signature="e2e-buy",
            event_type=WalletEventType.SWAP_BUY,
            token_address="E2EToken111",
            amount_ui=100.0,
            value_usd=50.0,
            value_usd_status=ValueUsdStatus.KNOWN,
            block_time=t0,
        )
    )
    ingest_wallet_transaction(
        WalletTransactionIn(
            chain=Chain.SOLANA,
            wallet_address="E2EWallet1111111111111111111111111111",
            tx_signature="e2e-sell",
            event_type=WalletEventType.SWAP_SELL,
            token_address="E2EToken111",
            amount_ui=100.0,
            value_usd=75.0,
            value_usd_status=ValueUsdStatus.KNOWN,
            block_time=t0 + timedelta(hours=2),
        )
    )
    txs = list_wallet_transactions(
        chain="solana",
        wallet_address="E2EWallet1111111111111111111111111111",
        limit=50,
    )
    assert len(txs) >= 2

    # 3. Performance ledger
    snap = compute_performance(
        "solana", "E2EWallet1111111111111111111111111111", txs
    )
    assert snap.sample_size >= 2
    assert snap.realized_pnl_usd is not None
    assert abs(snap.realized_pnl_usd - 25.0) < 1e-6

    # 4. Worker snapshot cycle
    run_wallet_performance_cycle(limit_wallets=50)
    cached = get_latest_snapshot("solana", "E2EWallet1111111111111111111111111111")
    assert cached is not None
    assert cached.get("sample_size", 0) >= 2

    # 5. Outcomes schedule + apply snapshot (no live DexScreener)
    ids = schedule_outcome_jobs(
        "e2e-eval-1",
        "solana",
        "E2EToken111",
        reference_price_usd=1.0,
        reference_liquidity_usd=5_000.0,
    )
    assert len(ids) == 6
    row = list_outcomes("e2e-eval-1")[0]
    apply_market_snapshot(row, price_usd=2.2, liquidity_usd=4_500.0)
    assert row["outcome_status"] == "complete"
    assert row["multiples_reached"]["reached_2x"] is True
    summary = summarize_outcomes("e2e-eval-1")
    assert summary["complete"] >= 1
    assert "wallet" in summary["disclaimer"].lower() or "not" in summary["disclaimer"].lower()


def test_offline_e2e_qualification_engine_happy_path():
    engine = create_engine()
    identity = TokenIdentity(
        chain=Chain.BASE,
        address="0xe2e0000000000000000000000000000000000001",
        name="E2E",
        symbol="E2E",
    )
    now = datetime.now(timezone.utc)
    evidence = [
        EvidenceItem(
            key="mint_meta",
            source="test",
            retrieval_status="ok",
            value={"ok": True},
            observed_at=now,
        ),
        EvidenceItem(
            key="liquidity",
            source="test",
            retrieval_status="ok",
            value={"usd": 80_000},
            observed_at=now,
        ),
    ]
    checks = [
        CheckOutcome(
            check_id="identity_valid",
            name="Identity valid",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="mint_authority_renounced_or_safe",
            name="Mint authority",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="freeze_authority_safe",
            name="Freeze authority",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="no_blacklist_or_pause_trap",
            name="No blacklist",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="sellability_not_failed",
            name="Sellability",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="liquidity_present",
            name="Liquidity present",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
    ]
    sellability = SellabilityEvidence(
        status=SellabilityStatus.VERIFIED,
        method="quote",
        route_available=True,
    )
    liquidity = [
        LiquiditySnapshot(
            pool_address="pool1",
            dex="test",
            liquidity_usd=80_000.0,
            observed_at=now,
            source="test",
        )
    ]
    holders = HolderSnapshot(
        top_holder_pct=8.0,
        top10_pct=35.0,
        creator_pct=2.0,
        unique_holders=400,
        observed_at=now,
        source="test",
    )
    decision = engine.evaluate(
        identity=identity,
        evidence=evidence,
        checks=checks,
        sellability=sellability,
        liquidity=liquidity,
        holders=holders,
    )
    assert decision.final_status.value in (
        "QUALIFIED_TRADE_CANDIDATE",
        "SECURITY_QUALIFIED_NO_TRADE_SIGNAL",
        "MONITOR_NOT_YET_QUALIFIED",
    )
    assert decision.scores.manipulation_risk_score < 55
    # Reproducible
    d2 = engine.evaluate(
        identity=identity,
        evidence=evidence,
        checks=checks,
        sellability=sellability,
        liquidity=liquidity,
        holders=holders,
        evaluation_id=decision.evaluation_id,
    )
    assert d2.final_status == decision.final_status
    assert d2.scores.security_risk_score == decision.scores.security_risk_score


def test_offline_e2e_safety_flags_remain_off():
    s = build_public_settings()
    assert s["live_trading"] is False
    assert s["auto_trading_enabled"] is False
    assert s["wallet_connection_enabled"] is False
    assert s["wallet_signing"] is False
    assert s["public_wallet_tracking_enabled"] is True
