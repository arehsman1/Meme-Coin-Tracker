"""Unit tests for sellability assessment (no network)."""

from meme_tracker.models.enums import CheckResult, SellabilityStatus
from meme_tracker.sellability.assessment import assess_from_dex_pairs


def test_no_pairs_unknown():
    ev, result, reason = assess_from_dex_pairs(None)
    assert ev.status == SellabilityStatus.UNKNOWN
    assert result == CheckResult.UNKNOWN


def test_zero_liq_zero_sells_failed():
    pairs = [
        {
            "liquidity": {"usd": 0},
            "txns": {"h24": {"buys": 0, "sells": 0}},
        }
    ]
    ev, result, _ = assess_from_dex_pairs(pairs)
    assert ev.status == SellabilityStatus.FAILED
    assert result == CheckResult.FAIL


def test_buy_heavy_zero_sell_failed():
    pairs = [
        {
            "liquidity": {"usd": 800},
            "txns": {"h24": {"buys": 40, "sells": 0}},
        }
    ]
    ev, result, _ = assess_from_dex_pairs(pairs)
    assert ev.status == SellabilityStatus.FAILED
    assert result == CheckResult.FAIL


def test_verified_with_sells_and_liq():
    pairs = [
        {
            "liquidity": {"usd": 25000},
            "txns": {"h24": {"buys": 100, "sells": 80}},
        }
    ]
    ev, result, reason = assess_from_dex_pairs(pairs)
    assert ev.status == SellabilityStatus.VERIFIED
    assert result == CheckResult.PASS
    assert "sells" in reason.lower() or "Observed" in reason


def test_limited_thin():
    pairs = [
        {
            "liquidity": {"usd": 900},
            "txns": {"h1": {"buys": 3, "sells": 2}},
        }
    ]
    ev, result, _ = assess_from_dex_pairs(pairs)
    assert ev.status == SellabilityStatus.LIMITED
    assert result == CheckResult.PASS
