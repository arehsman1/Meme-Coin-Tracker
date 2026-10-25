"""Worker recovery + wallet performance cycle tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meme_tracker.models.enums import (
    Chain,
    ValueUsdStatus,
    WalletEventType,
    WalletSource,
)
from meme_tracker.wallets.models import TrackedWalletCreate, WalletTransactionIn
from meme_tracker.wallets.repository import (
    ingest_wallet_transaction,
    upsert_tracked_wallet,
)
from meme_tracker.worker.recovery import FailureTracker
from meme_tracker.worker.wallet_jobs import (
    clear_snapshot_cache,
    get_latest_snapshot,
    run_wallet_performance_cycle,
)


def test_failure_tracker_backoff():
    t = FailureTracker(max_attempts=3, base_cooldown_seconds=1.0)
    job = {"id": "j1", "job_type": "evaluate"}
    assert t.should_skip(job) is False
    t.record_failure(job, "boom")
    assert t.should_skip(job) is True  # cooldown active
    # After cooldown expires
    rec = t._records["j1"]
    rec.cooldown_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert t.should_skip(job) is False
    t.record_failure(job, "boom2")
    t.record_failure(job, "boom3")
    assert t.should_skip(job) is True  # max attempts
    assert t.stats()["exhausted"] >= 1
    t.record_success(job)
    assert t.should_skip(job) is False


def test_wallet_performance_cycle_computes_snapshot():
    clear_snapshot_cache()
    upsert_tracked_wallet(
        TrackedWalletCreate(
            chain=Chain.SOLANA,
            address="PerfWallet111111111111111111111111111",
            label="perf-test",
            source=WalletSource.MANUAL,
        )
    )
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ingest_wallet_transaction(
        WalletTransactionIn(
            chain=Chain.SOLANA,
            wallet_address="PerfWallet111111111111111111111111111",
            tx_signature="perf-buy-1",
            event_type=WalletEventType.SWAP_BUY,
            token_address="TokenPerf111",
            amount_ui=10.0,
            value_usd=100.0,
            value_usd_status=ValueUsdStatus.KNOWN,
            block_time=t0,
        )
    )
    ingest_wallet_transaction(
        WalletTransactionIn(
            chain=Chain.SOLANA,
            wallet_address="PerfWallet111111111111111111111111111",
            tx_signature="perf-sell-1",
            event_type=WalletEventType.SWAP_SELL,
            token_address="TokenPerf111",
            amount_ui=10.0,
            value_usd=120.0,
            value_usd_status=ValueUsdStatus.KNOWN,
            block_time=t0 + timedelta(hours=1),
        )
    )
    summary = run_wallet_performance_cycle(limit_wallets=50)
    assert summary["snapshots_computed"] >= 1
    snap = get_latest_snapshot("solana", "PerfWallet111111111111111111111111111")
    assert snap is not None
    assert snap["sample_size"] >= 2
    assert "disclaimer" in snap
