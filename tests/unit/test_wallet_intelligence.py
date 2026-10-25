"""Phase 4 — Wallet Intelligence foundation tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from meme_tracker.models.enums import Chain, ValueUsdStatus, WalletEventType, WalletSource
from meme_tracker.wallets.classification import (
    DEFAULT_SIGNIFICANT_SWAP_USD,
    apply_significance,
    classify_event_type,
    decide_significance,
)
from meme_tracker.wallets.models import TrackedWalletCreate, WalletTransactionIn
from meme_tracker.wallets.service import get_wallet_store, reset_wallet_store


@pytest.fixture(autouse=True)
def clean_store():
    reset_wallet_store()
    yield
    reset_wallet_store()


def test_classify_swap_buy():
    assert (
        classify_event_type(is_dex_swap=True, is_buy_side=True)
        == WalletEventType.SWAP_BUY
    )


def test_classify_transfer_not_swap():
    assert (
        classify_event_type(is_dex_swap=False, is_buy_side=True, has_token_movement=True)
        == WalletEventType.TRANSFER_IN
    )


def test_classify_failed():
    assert (
        classify_event_type(is_dex_swap=True, is_buy_side=True, is_failed=True)
        == WalletEventType.FAILED
    )


def test_significance_unknown_value_not_significant():
    d = decide_significance(None, ValueUsdStatus.UNKNOWN, threshold_usd=500)
    assert d.is_significant is False
    assert d.reason == "value_usd_unknown"


def test_significance_above_threshold():
    d = decide_significance(1200.0, ValueUsdStatus.KNOWN, threshold_usd=500)
    assert d.is_significant is True


def test_significance_below_threshold():
    d = decide_significance(100.0, ValueUsdStatus.KNOWN, threshold_usd=500)
    assert d.is_significant is False


def test_significance_transfer_not_eligible():
    d = decide_significance(
        5000.0,
        ValueUsdStatus.KNOWN,
        threshold_usd=500,
        event_type=WalletEventType.TRANSFER_IN,
    )
    assert d.is_significant is False
    assert d.reason == "event_type_not_swap"


def test_default_threshold_is_500():
    assert DEFAULT_SIGNIFICANT_SWAP_USD == 500.0


def test_track_wallet_manual():
    store = get_wallet_store()
    w = store.upsert_tracked(
        TrackedWalletCreate(
            chain=Chain.SOLANA,
            address="SomeSolanaWallet1111111111111111111111111",
            label="Test whale",
            source=WalletSource.MANUAL,
        )
    )
    assert w.id is not None
    assert w.is_active is True
    assert w.chain == Chain.SOLANA
    listed = store.list_tracked()
    assert len(listed) == 1


def test_evm_address_normalised_lowercase():
    store = get_wallet_store()
    w = store.upsert_tracked(
        TrackedWalletCreate(
            chain=Chain.ETHEREUM,
            address="0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
            source=WalletSource.MANUAL,
        )
    )
    assert w.address == "0xabcdef0123456789abcdef0123456789abcdef01"


def test_solana_address_case_preserved():
    store = get_wallet_store()
    addr = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    w = store.upsert_tracked(
        TrackedWalletCreate(chain=Chain.SOLANA, address=addr, source=WalletSource.MANUAL)
    )
    assert w.address == addr


def test_ingest_and_deduplicate():
    store = get_wallet_store()
    store.upsert_tracked(
        TrackedWalletCreate(
            chain=Chain.BASE,
            address="0xabc1230000000000000000000000000000000001",
            source=WalletSource.MANUAL,
        )
    )
    tx = WalletTransactionIn(
        chain=Chain.BASE,
        wallet_address="0xabc1230000000000000000000000000000000001",
        tx_signature="0xdeadbeef",
        event_type=WalletEventType.SWAP_BUY,
        value_usd=800.0,
        value_usd_status=ValueUsdStatus.KNOWN,
        block_time=datetime.now(timezone.utc),
    )
    r1 = store.ingest_transaction(tx)
    r2 = store.ingest_transaction(tx)
    assert r1 is not None
    assert r1.is_significant is True
    assert r2 is None  # duplicate
    assert len(store.list_transactions()) == 1


def test_incoming_transfer_not_classified_as_buy():
    """Master requirement: do not treat every incoming transfer as a purchase."""
    store = get_wallet_store()
    store.upsert_tracked(
        TrackedWalletCreate(
            chain=Chain.SOLANA,
            address="WalletAAAA",
            source=WalletSource.MANUAL,
        )
    )
    tx = WalletTransactionIn(
        chain=Chain.SOLANA,
        wallet_address="WalletAAAA",
        tx_signature="sig1",
        event_type=WalletEventType.TRANSFER_IN,
        value_usd=10_000.0,
        value_usd_status=ValueUsdStatus.KNOWN,
    )
    record = store.ingest_transaction(tx)
    assert record is not None
    assert record.event_type == WalletEventType.TRANSFER_IN
    assert record.is_significant is False  # transfers are never "significant swaps"


def test_profile_updated_after_ingest():
    store = get_wallet_store()
    addr = "0xprofile00000000000000000000000000000001"
    store.upsert_tracked(
        TrackedWalletCreate(chain=Chain.ETHEREUM, address=addr, source=WalletSource.MANUAL)
    )
    store.ingest_transaction(
        WalletTransactionIn(
            chain=Chain.ETHEREUM,
            wallet_address=addr,
            tx_signature="0x1",
            event_type=WalletEventType.SWAP_SELL,
            value_usd=600.0,
            value_usd_status=ValueUsdStatus.KNOWN,
        )
    )
    profile = store.get_profile(Chain.ETHEREUM, addr)
    assert profile is not None
    assert profile.observed_trade_count == 1
    assert profile.observed_significant_count == 1
    assert profile.data_completeness_score == 100


def test_deactivate_wallet():
    store = get_wallet_store()
    store.upsert_tracked(
        TrackedWalletCreate(
            chain=Chain.SOLANA, address="ToPause", source=WalletSource.MANUAL
        )
    )
    assert store.deactivate(Chain.SOLANA, "ToPause") is True
    assert len(store.list_tracked(active_only=True)) == 0
    assert len(store.list_tracked(active_only=False)) == 1


def test_apply_significance_mutates_copy():
    tx = WalletTransactionIn(
        chain=Chain.SOLANA,
        wallet_address="W",
        tx_signature="s",
        event_type=WalletEventType.SWAP_BUY,
        value_usd=1000.0,
        value_usd_status=ValueUsdStatus.KNOWN,
    )
    out = apply_significance(tx, threshold_usd=500)
    assert out.is_significant is True
    assert tx.is_significant is False  # original unchanged


def test_repository_memory_mode_track_and_list():
    from meme_tracker.wallets.repository import (
        list_tracked_wallets,
        upsert_tracked_wallet,
        wallet_storage_mode,
    )
    from meme_tracker.wallets.models import TrackedWalletCreate
    from meme_tracker.models.enums import Chain, WalletSource
    from meme_tracker.wallets.service import reset_wallet_store

    reset_wallet_store()
    assert wallet_storage_mode() in ("memory", "supabase_degraded", "supabase")
    w = upsert_tracked_wallet(
        TrackedWalletCreate(
            chain=Chain.BASE,
            address="0xRepoTest00000000000000000000000000000001",
            label="repo test",
            source=WalletSource.MANUAL,
        )
    )
    assert w.address.startswith("0x")
    listed = list_tracked_wallets(chain="base", active_only=True)
    assert any(x.address == w.address for x in listed)


def test_repository_ingest_via_public_api():
    from meme_tracker.wallets.repository import (
        ingest_wallet_transaction,
        list_wallet_transactions,
        upsert_tracked_wallet,
    )
    from meme_tracker.wallets.models import TrackedWalletCreate, WalletTransactionIn
    from meme_tracker.models.enums import Chain, ValueUsdStatus, WalletEventType, WalletSource
    from meme_tracker.wallets.service import reset_wallet_store

    reset_wallet_store()
    upsert_tracked_wallet(
        TrackedWalletCreate(
            chain=Chain.SOLANA,
            address="RepoSolWallet",
            source=WalletSource.MANUAL,
        )
    )
    tx = WalletTransactionIn(
        chain=Chain.SOLANA,
        wallet_address="RepoSolWallet",
        tx_signature="reposig1",
        event_type=WalletEventType.SWAP_BUY,
        value_usd=900.0,
        value_usd_status=ValueUsdStatus.KNOWN,
    )
    r1 = ingest_wallet_transaction(tx)
    r2 = ingest_wallet_transaction(tx)
    assert r1 is not None and r1.is_significant is True
    assert r2 is None
    items = list_wallet_transactions(chain="solana", wallet_address="RepoSolWallet")
    assert len(items) == 1


def test_should_send_wallet_alert_significant_buy():
    from meme_tracker.notifications.outbox import should_send_wallet_significant_alert

    assert should_send_wallet_significant_alert(
        event_type="SWAP_BUY", is_significant=True, value_usd_status="KNOWN"
    )


def test_should_not_send_transfer_even_if_large():
    from meme_tracker.notifications.outbox import should_send_wallet_significant_alert

    assert not should_send_wallet_significant_alert(
        event_type="TRANSFER_IN", is_significant=True, value_usd_status="KNOWN"
    )


def test_should_not_send_unknown_usd():
    from meme_tracker.notifications.outbox import should_send_wallet_significant_alert

    assert not should_send_wallet_significant_alert(
        event_type="SWAP_SELL", is_significant=True, value_usd_status="UNKNOWN"
    )


def test_wallet_alert_payload_includes_token_status_disclaimer():
    from meme_tracker.notifications.outbox import (
        build_wallet_significant_alert_payload,
        format_wallet_significant_telegram_text,
    )

    payload = build_wallet_significant_alert_payload(
        chain="solana",
        wallet_address="WalletXYZ",
        event_type="SWAP_BUY",
        tx_signature="sigabc",
        value_usd=1200.0,
        value_usd_status="KNOWN",
        token_address="TokenMint111",
        token_qualification_status="REJECTED_CRITICAL_RISK",
        wallet_label="Whale A",
        threshold_usd=500.0,
    )
    assert payload["type"] == "wallet_significant_swap"
    assert payload["token_qualification_status"] == "REJECTED_CRITICAL_RISK"
    text = format_wallet_significant_telegram_text(payload)
    assert "WALLET BUY" in text
    assert "REJECTED_CRITICAL_RISK" in text
    assert "independent of token qualification" in text.lower() or "Independent of token" in text


def test_ingest_significant_enqueues_outbox():
    """Significant swap must land in notification outbox (memory mode)."""
    from meme_tracker.db.repository import _mem_outbox, _lock
    from meme_tracker.wallets.repository import ingest_wallet_transaction, upsert_tracked_wallet
    from meme_tracker.wallets.models import TrackedWalletCreate, WalletTransactionIn
    from meme_tracker.models.enums import Chain, ValueUsdStatus, WalletEventType, WalletSource
    from meme_tracker.wallets.service import reset_wallet_store

    reset_wallet_store()
    with _lock:
        _mem_outbox.clear()

    upsert_tracked_wallet(
        TrackedWalletCreate(
            chain=Chain.ETHEREUM,
            address="0xalertwallet000000000000000000000000000001",
            label="Alert Whale",
            source=WalletSource.MANUAL,
        )
    )
    record = ingest_wallet_transaction(
        WalletTransactionIn(
            chain=Chain.ETHEREUM,
            wallet_address="0xalertwallet000000000000000000000000000001",
            tx_signature="0xalerttx1",
            event_type=WalletEventType.SWAP_BUY,
            token_address="0xtokenrejected00000000000000000000000001",
            value_usd=2500.0,
            value_usd_status=ValueUsdStatus.KNOWN,
        )
    )
    assert record is not None
    assert record.is_significant is True

    with _lock:
        wallet_msgs = [
            m
            for m in _mem_outbox
            if m.get("notification_type") == "wallet_significant_swap"
        ]
    assert len(wallet_msgs) >= 1
    payload = wallet_msgs[-1].get("payload") or {}
    assert payload.get("event_type") == "SWAP_BUY"
    assert payload.get("value_usd") == 2500.0


def test_ingest_transfer_does_not_enqueue_wallet_alert():
    from meme_tracker.db.repository import _mem_outbox, _lock
    from meme_tracker.wallets.repository import ingest_wallet_transaction, upsert_tracked_wallet
    from meme_tracker.wallets.models import TrackedWalletCreate, WalletTransactionIn
    from meme_tracker.models.enums import Chain, ValueUsdStatus, WalletEventType, WalletSource
    from meme_tracker.wallets.service import reset_wallet_store

    reset_wallet_store()
    with _lock:
        before = len(
            [m for m in _mem_outbox if m.get("notification_type") == "wallet_significant_swap"]
        )

    upsert_tracked_wallet(
        TrackedWalletCreate(
            chain=Chain.SOLANA,
            address="NoAlertWallet",
            source=WalletSource.MANUAL,
        )
    )
    ingest_wallet_transaction(
        WalletTransactionIn(
            chain=Chain.SOLANA,
            wallet_address="NoAlertWallet",
            tx_signature="transferonly1",
            event_type=WalletEventType.TRANSFER_IN,
            value_usd=99999.0,
            value_usd_status=ValueUsdStatus.KNOWN,
        )
    )
    with _lock:
        after = len(
            [m for m in _mem_outbox if m.get("notification_type") == "wallet_significant_swap"]
        )
    assert after == before
