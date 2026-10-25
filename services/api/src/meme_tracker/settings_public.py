"""Public, non-secret settings payload for Mini App and clients."""

from __future__ import annotations

from typing import Any

from meme_tracker import ENGINE_VERSION, POLICY_VERSION
from meme_tracker.wallets.classification import DEFAULT_SIGNIFICANT_SWAP_USD
from meme_tracker.trading.gates import trading_status


def build_public_settings() -> dict[str, Any]:
    """
    Safe runtime posture. Independent toggles:
    - public wallet tracking can be ON while personal wallet connection stays OFF
    - auto-trading / signing stay OFF at this surface
    """
    return {
        "supported_chains": ["solana", "ethereum", "base"],
        "engine_version": ENGINE_VERSION,
        "policy_version": POLICY_VERSION,
        "live_trading": False,
        "automatic_buying": False,
        "automatic_selling": False,
        "wallet_signing": False,
        "auto_trading_enabled": False,
        "wallet_connection_enabled": False,
        "public_wallet_tracking_enabled": True,
        "significant_swap_usd_threshold_default": DEFAULT_SIGNIFICANT_SWAP_USD,
        "outcome_horizons_minutes": [5, 15, 60, 360, 1440, 10080],
        "trading": trading_status(),
        "safety_notes": [
            "Public wallet tracking does not require connecting a personal wallet.",
            "Auto-trading and wallet signing remain OFF until explicitly enabled by an admin with live credentials — not by this API.",
            "Qualification and alerts are not financial advice.",
        ],
    }
