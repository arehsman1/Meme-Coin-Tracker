"""Phase 11 — trading remains hard-blocked."""

import pytest

from meme_tracker.trading.gates import (
    TRADING_FEATURE_ENABLED,
    TradingBlockedError,
    assert_trading_disabled,
    can_execute_trade,
    can_sign_wallet,
    reject_on_flags,
    trading_status,
)


def test_master_flag_is_false():
    assert TRADING_FEATURE_ENABLED is False


def test_cannot_execute_or_sign():
    ok, msg = can_execute_trade()
    assert ok is False
    assert "False" in msg or "blocked" in msg.lower() or "not" in msg.lower()
    ok2, _ = can_sign_wallet()
    assert ok2 is False


def test_assert_raises():
    with pytest.raises(TradingBlockedError):
        assert_trading_disabled("buy")


def test_reject_on_flags_blocks_on():
    with pytest.raises(ValueError, match="live_trading"):
        reject_on_flags({"live_trading": "ON", "continuous_scanning": "ON"})


def test_reject_forces_off():
    out = reject_on_flags({"wallet_signing": "OFF", "continuous_scanning": "ON"})
    assert out["wallet_signing"] == "OFF"
    assert out["continuous_scanning"] == "ON"


def test_trading_status_shape():
    s = trading_status()
    assert s["feature_flag"] is False
    assert s["can_execute_trade"] is False
    assert s["can_sign_wallet"] is False
    assert s["phase"] == 11
