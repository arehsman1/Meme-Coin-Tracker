"""Phase 5 — position ledger and performance tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meme_tracker.models.enums import Chain, ValueUsdStatus, WalletEventType
from meme_tracker.wallets.ledger import (
    SCORING_RULE_VERSION,
    apply_swap_to_position,
    build_positions_from_transactions,
    compute_performance,
    score_components,
)
from meme_tracker.wallets.models import WalletTransaction
from meme_tracker.wallets.relationships import detect_coordinated_token_buys
from meme_tracker.models.enums import RelationshipConfidence


def _tx(**kwargs) -> WalletTransaction:
    base = dict(
        chain=Chain.SOLANA,
        wallet_address="W1",
        tx_signature="sig",
        event_type=WalletEventType.SWAP_BUY,
        token_address="TokenA",
        amount_ui=10.0,
        value_usd=100.0,
        value_usd_status=ValueUsdStatus.KNOWN,
        is_significant=False,
    )
    base.update(kwargs)
    return WalletTransaction(**base)


def test_buy_then_full_sell_realizes_pnl():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    buy = _tx(
        tx_signature="b1",
        event_type=WalletEventType.SWAP_BUY,
        amount_ui=10.0,
        value_usd=100.0,
        block_time=t0,
    )
    sell = _tx(
        tx_signature="s1",
        event_type=WalletEventType.SWAP_SELL,
        amount_ui=10.0,
        value_usd=150.0,
        block_time=t0 + timedelta(hours=1),
    )
    positions = build_positions_from_transactions("solana", "W1", [buy, sell])
    pos = positions["TokenA"]
    assert pos.qty_open == 0
    assert pos.realized_pnl_usd == 50.0
    assert pos.realized_pnl_status == "KNOWN"
    assert pos.closed_wins == 1


def test_partial_exit_average_cost():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    buy = _tx(
        tx_signature="b1",
        amount_ui=10.0,
        value_usd=100.0,
        block_time=t0,
    )
    sell = _tx(
        tx_signature="s1",
        event_type=WalletEventType.SWAP_SELL,
        amount_ui=4.0,
        value_usd=60.0,  # sold 4 @ higher price
        block_time=t0 + timedelta(minutes=30),
    )
    positions = build_positions_from_transactions("solana", "W1", [buy, sell])
    pos = positions["TokenA"]
    assert abs(pos.qty_open - 6.0) < 1e-9
    # avg cost 10/unit, sold 4 cost 40, proceeds 60 → pnl 20
    assert abs(pos.realized_pnl_usd - 20.0) < 1e-6
    assert abs(pos.cost_basis_usd - 60.0) < 1e-6


def test_transfer_does_not_affect_ledger():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    transfer = _tx(
        tx_signature="t1",
        event_type=WalletEventType.TRANSFER_IN,
        amount_ui=100.0,
        value_usd=5000.0,
        block_time=t0,
    )
    positions = build_positions_from_transactions("solana", "W1", [transfer])
    assert positions == {}


def test_unknown_usd_limits_realized_status():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    buy = _tx(
        tx_signature="b1",
        amount_ui=5.0,
        value_usd=None,
        value_usd_status=ValueUsdStatus.UNKNOWN,
        block_time=t0,
    )
    sell = _tx(
        tx_signature="s1",
        event_type=WalletEventType.SWAP_SELL,
        amount_ui=5.0,
        value_usd=50.0,
        value_usd_status=ValueUsdStatus.KNOWN,
        block_time=t0 + timedelta(hours=1),
    )
    positions = build_positions_from_transactions("solana", "W1", [buy, sell])
    pos = positions["TokenA"]
    assert pos.qty_open == 0
    assert pos.realized_pnl_status in ("UNKNOWN", "PARTIAL")
    assert any("incomplete" in x or "unknown" in x for x in pos.limitations)


def test_sell_without_open_marks_incomplete():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sell = _tx(
        tx_signature="s1",
        event_type=WalletEventType.SWAP_SELL,
        amount_ui=3.0,
        value_usd=30.0,
        block_time=t0,
    )
    positions = build_positions_from_transactions("solana", "W1", [sell])
    pos = positions["TokenA"]
    assert any("sell_without_open" in x for x in pos.limitations)


def test_performance_exposes_sample_and_confidence():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    txs = [
        _tx(tx_signature="b1", amount_ui=1, value_usd=10, block_time=t0),
        _tx(
            tx_signature="s1",
            event_type=WalletEventType.SWAP_SELL,
            amount_ui=1,
            value_usd=12,
            block_time=t0 + timedelta(minutes=5),
        ),
    ]
    snap = compute_performance("solana", "W1", txs)
    assert snap.sample_size == 2
    assert snap.closed_trade_count >= 1
    assert snap.data_completeness_score == 100
    assert "small_sample" in snap.limitations
    assert snap.scoring_rule_version == SCORING_RULE_VERSION
    assert "composite_quality" in snap.components


def test_small_sample_win_rate_does_not_inflate_sample_factor():
    comps = score_components(
        win_rate=1.0, closed=2, completeness=100, realized_pnl=500.0, trade_count=2
    )
    # sample_factor = 2/20 = 0.1 so win component is modest
    assert comps["sample_factor"] == 0.1
    assert comps["win_rate_weighted"] < 10


def test_coordinated_buy_is_possible_not_confirmed():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    txs = [
        _tx(wallet_address="A", tx_signature="1", block_time=t0),
        _tx(wallet_address="B", tx_signature="2", block_time=t0 + timedelta(seconds=30)),
    ]
    edges = detect_coordinated_token_buys("solana", txs, window_seconds=300)
    assert len(edges) >= 1
    assert edges[0].confidence == RelationshipConfidence.POSSIBLE
    assert "not prove common ownership" in edges[0].notes.lower() or "Does not prove" in edges[0].notes
