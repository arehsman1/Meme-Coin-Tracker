"""Deterministic classification of wallet activity.

Rules:
- Never treat an ordinary token transfer as a SWAP_BUY / SWAP_SELL.
- Only mark value_usd as KNOWN when a reliable price was available at event time.
- Significant-swap threshold defaults to $500 USD and is configurable.
"""

from __future__ import annotations

from meme_tracker.models.enums import ValueUsdStatus, WalletEventType
from meme_tracker.wallets.models import SignificantSwapDecision, WalletTransactionIn

# Global default (can be overridden per-wallet or via wallet_alert_settings)
DEFAULT_SIGNIFICANT_SWAP_USD = 500.0


def classify_event_type(
    *,
    is_dex_swap: bool,
    is_buy_side: bool | None,
    is_liquidity: bool = False,
    is_failed: bool = False,
    has_token_movement: bool = True,
) -> WalletEventType:
    """Map raw provider signals to an explicit WalletEventType.

    Callers must supply the evidence flags; this function does not invent them.
    """
    if is_failed:
        return WalletEventType.FAILED
    if is_liquidity:
        # Caller should further distinguish ADD vs REMOVE when possible.
        return WalletEventType.LIQUIDITY_ADD  # placeholder; refine with side
    if is_dex_swap:
        if is_buy_side is True:
            return WalletEventType.SWAP_BUY
        if is_buy_side is False:
            return WalletEventType.SWAP_SELL
        return WalletEventType.UNKNOWN
    if has_token_movement:
        # Without DEX evidence we only know direction of transfer.
        if is_buy_side is True:
            return WalletEventType.TRANSFER_IN
        if is_buy_side is False:
            return WalletEventType.TRANSFER_OUT
    return WalletEventType.UNKNOWN


def decide_significance(
    value_usd: float | None,
    value_usd_status: ValueUsdStatus,
    threshold_usd: float = DEFAULT_SIGNIFICANT_SWAP_USD,
    event_type: WalletEventType | None = None,
) -> SignificantSwapDecision:
    """Apply the significant-swap rule.

    UNKNOWN valuation → not significant (we refuse to invent a USD amount).
    Only SWAP_BUY / SWAP_SELL are eligible for the significant flag by default.
    """
    if event_type is not None and event_type not in (
        WalletEventType.SWAP_BUY,
        WalletEventType.SWAP_SELL,
    ):
        return SignificantSwapDecision(
            is_significant=False,
            value_usd=value_usd,
            value_usd_status=value_usd_status,
            threshold_used=threshold_usd,
            reason="event_type_not_swap",
        )

    if value_usd_status == ValueUsdStatus.UNKNOWN or value_usd is None:
        return SignificantSwapDecision(
            is_significant=False,
            value_usd=None,
            value_usd_status=ValueUsdStatus.UNKNOWN,
            threshold_used=threshold_usd,
            reason="value_usd_unknown",
        )

    if value_usd >= threshold_usd:
        return SignificantSwapDecision(
            is_significant=True,
            value_usd=value_usd,
            value_usd_status=value_usd_status,
            threshold_used=threshold_usd,
            reason=f"value_usd ({value_usd}) >= threshold ({threshold_usd})",
        )

    return SignificantSwapDecision(
        is_significant=False,
        value_usd=value_usd,
        value_usd_status=value_usd_status,
        threshold_used=threshold_usd,
        reason=f"value_usd ({value_usd}) < threshold ({threshold_usd})",
    )


def apply_significance(
    tx: WalletTransactionIn,
    threshold_usd: float = DEFAULT_SIGNIFICANT_SWAP_USD,
) -> WalletTransactionIn:
    """Return a copy of the transaction with is_significant set correctly."""
    decision = decide_significance(
        value_usd=tx.value_usd,
        value_usd_status=tx.value_usd_status,
        threshold_usd=threshold_usd,
        event_type=tx.event_type,
    )
    data = tx.model_dump()
    data["is_significant"] = decision.is_significant
    data["value_usd"] = decision.value_usd
    data["value_usd_status"] = decision.value_usd_status
    return WalletTransactionIn(**data)
