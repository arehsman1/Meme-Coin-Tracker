"""
Phase 11 safety gates.

Live trading, automatic buy/sell, and wallet signing are OFF.
This module is the single enforcement point for any future trade path.

Unlocking requires a deliberate code + config change — not a Mini App toggle
and not a single admin API field flip in this release.
"""

from __future__ import annotations

from typing import Any

# Master kill switch for this codebase revision.
# Must remain False unless a future release explicitly ships trading.
TRADING_FEATURE_ENABLED = False

# Flags that must never be set ON via public or admin APIs in this release.
BLOCKED_ON_FLAGS = (
    "live_trading",
    "automatic_buying",
    "automatic_selling",
    "wallet_signing",
    "auto_trading_enabled",
    "wallet_connection_enabled",
)


class TradingBlockedError(RuntimeError):
    """Raised when any code path attempts live trading or signing."""


def can_enable_trading() -> tuple[bool, str]:
    if not TRADING_FEATURE_ENABLED:
        return False, "TRADING_FEATURE_ENABLED is False in this release"
    return False, "Trading unlock criteria not implemented — remains blocked"


def can_execute_trade(*, chain: str | None = None, reason: str | None = None) -> tuple[bool, str]:
    ok, msg = can_enable_trading()
    if not ok:
        return False, msg
    return False, "Trade execution not implemented"


def can_sign_wallet() -> tuple[bool, str]:
    if not TRADING_FEATURE_ENABLED:
        return False, "Wallet signing disabled (TRADING_FEATURE_ENABLED=False)"
    return False, "Wallet signing not implemented"


def assert_trading_disabled(action: str = "trade") -> None:
    """Call at the start of any future trade/sign function."""
    ok, msg = can_execute_trade()
    if not ok:
        raise TradingBlockedError(f"{action} blocked: {msg}")


def reject_on_flags(data: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize admin/settings payloads.
    Any attempt to set a blocked flag to ON raises ValueError.
    Provided blocked keys are forced to OFF.
    """
    out = dict(data)
    for k in BLOCKED_ON_FLAGS:
        if k not in out:
            continue
        val = out[k]
        if val in (True, "ON", "on", "true", "True", 1):
            raise ValueError(f"{k} cannot be enabled in this release")
        if val in (False, "OFF", "off", "false", "False", 0):
            out[k] = "OFF" if isinstance(val, str) or val is False else False
    return out


def trading_status() -> dict[str, Any]:
    enable_ok, enable_msg = can_enable_trading()
    exec_ok, exec_msg = can_execute_trade()
    sign_ok, sign_msg = can_sign_wallet()
    return {
        "feature_flag": TRADING_FEATURE_ENABLED,
        "can_enable_trading": enable_ok,
        "can_execute_trade": exec_ok,
        "can_sign_wallet": sign_ok,
        "messages": {
            "enable": enable_msg,
            "execute": exec_msg,
            "sign": sign_msg,
        },
        "blocked_on_flags": list(BLOCKED_ON_FLAGS),
        "phase": 11,
        "note": (
            "Public wallet tracking does not require personal wallet connection. "
            "Trading remains OFF independently of tracking."
        ),
    }
