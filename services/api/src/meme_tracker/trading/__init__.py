"""Optional trading subsystem — intentionally disabled in this release."""

from meme_tracker.trading.gates import (
    TRADING_FEATURE_ENABLED,
    TradingBlockedError,
    assert_trading_disabled,
    can_enable_trading,
    can_execute_trade,
    can_sign_wallet,
    trading_status,
)

__all__ = [
    "TRADING_FEATURE_ENABLED",
    "TradingBlockedError",
    "assert_trading_disabled",
    "can_enable_trading",
    "can_execute_trade",
    "can_sign_wallet",
    "trading_status",
]
