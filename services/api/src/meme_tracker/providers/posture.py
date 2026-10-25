"""Provider posture — honest DEGRADED / BLOCKED / READY without inventing connectivity."""

from __future__ import annotations

from typing import Any

from meme_tracker.config import get_settings
from meme_tracker.models.enums import Chain


def rpc_configured(chain: Chain) -> tuple[bool, str]:
    """Return (configured, reason). Never claims ready when URL is empty/placeholder."""
    s = get_settings()
    if chain == Chain.SOLANA:
        url = s.solana_rpc_url or ""
        if not url or "YOUR" in url.upper():
            return False, "SOLANA_RPC_URL missing or placeholder"
        # Public mainnet endpoint is allowed but marked limited
        if "mainnet-beta.solana.com" in url and not s.helius_api_key:
            return True, "public_solana_rpc_no_helius"
        return True, "configured"
    if chain == Chain.ETHEREUM:
        url = s.ethereum_rpc_url or ""
        if not url or url.endswith("/") or "YOUR" in url.upper():
            return False, "ETHEREUM_RPC_URL / Alchemy key missing"
        return True, "configured"
    if chain == Chain.BASE:
        url = s.base_rpc_url or ""
        if not url or url.endswith("/") or "YOUR" in url.upper():
            return False, "BASE_RPC_URL missing"
        return True, "configured"
    return False, "unsupported_chain"


def provider_posture() -> dict[str, Any]:
    """
    Summary for /ready and operators.
    DexScreener needs no key; RPCs may be DEGRADED without keys.
    """
    chains = {}
    for c in (Chain.SOLANA, Chain.ETHEREUM, Chain.BASE):
        ok, reason = rpc_configured(c)
        chains[c.value] = {
            "rpc_configured": ok,
            "status": "READY" if ok else "BLOCKED",
            "reason": reason,
        }
        if ok and reason == "public_solana_rpc_no_helius":
            chains[c.value]["status"] = "DEGRADED"

    return {
        "dexscreener": {
            "configured": True,
            "status": "READY",
            "reason": "public API, no key required",
        },
        "chains": chains,
        "note": (
            "Discovery can run on DexScreener alone. "
            "Full qualification depth needs chain RPC credentials. "
            "Missing credentials produce UNKNOWN evidence, not silent PASS."
        ),
    }
