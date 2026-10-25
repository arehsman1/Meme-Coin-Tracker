"""Phase 6 — manipulation / concentration risk tests."""

from __future__ import annotations

from datetime import datetime, timezone

from meme_tracker.models.enums import CheckResult
from meme_tracker.models.evidence import HolderSnapshot, LiquiditySnapshot
from meme_tracker.risk.manipulation import (
    MarketActivitySignals,
    analyze_manipulation,
)


def test_missing_holders_unknown_not_pass():
    a = analyze_manipulation(holders=None, liquidity=None)
    assert "holders" in a.data_gaps
    ids = {c.check_id: c for c in a.checks}
    assert ids["holder_concentration_known"].result == CheckResult.UNKNOWN


def test_high_top_holder_raises_risk():
    holders = HolderSnapshot(
        top_holder_pct=55.0,
        top10_pct=80.0,
        creator_pct=20.0,
        unique_holders=12,
        observed_at=datetime.now(timezone.utc),
        source="test",
    )
    a = analyze_manipulation(holders=holders, liquidity=[])
    assert a.manipulation_risk_score >= 50
    types = {f.signal_type for f in a.findings}
    assert "top_holder_concentration" in types
    assert "creator_concentration" in types
    fail = [c for c in a.checks if c.result == CheckResult.FAIL]
    assert any(c.check_id == "top_holder_not_excessive" for c in fail)


def test_healthy_holders_lower_risk():
    holders = HolderSnapshot(
        top_holder_pct=8.0,
        top10_pct=30.0,
        creator_pct=2.0,
        unique_holders=500,
        observed_at=datetime.now(timezone.utc),
        source="test",
    )
    liq = [
        LiquiditySnapshot(
            pool_address="p",
            dex="x",
            liquidity_usd=80_000.0,
            observed_at=datetime.now(timezone.utc),
            source="test",
        )
    ]
    a = analyze_manipulation(holders=holders, liquidity=liq)
    assert a.manipulation_risk_score < 30
    assert not any(f.severity == "high" for f in a.findings)


def test_buy_heavy_zero_sells():
    act = MarketActivitySignals(
        buy_volume_usd=50_000.0,
        sell_volume_usd=0.0,
        tx_count_window=20,
        unique_buyers_window=2,
        window_seconds=3600,
        source="test",
    )
    a = analyze_manipulation(holders=None, liquidity=None, activity=act)
    types = {f.signal_type for f in a.findings}
    assert "buy_heavy_zero_sells" in types
    assert "concentrated_buyers" in types


def test_does_not_invent_activity_when_absent():
    a = analyze_manipulation(
        holders=HolderSnapshot(
            top_holder_pct=10.0,
            observed_at=datetime.now(timezone.utc),
            source="t",
        ),
        liquidity=[],
        activity=None,
    )
    assert "market_activity" in a.data_gaps
    assert not any(f.signal_type == "buy_heavy_zero_sells" for f in a.findings)
