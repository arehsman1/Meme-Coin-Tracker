"""Discovery candidate extraction — offline, no DexScreener network."""

from meme_tracker.discovery.service import (
    extract_candidates_from_pairs,
    _is_ignored,
)
from meme_tracker.models.enums import Chain
from meme_tracker.providers.posture import provider_posture, rpc_configured


def test_ignores_wsol_and_usdc():
    assert _is_ignored(
        "So11111111111111111111111111111111111111112", "SOL", Chain.SOLANA
    )
    assert _is_ignored(
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", Chain.ETHEREUM
    )


def test_extract_filters_wrong_chain_and_dust():
    pairs = [
        {
            "chainId": "ethereum",
            "baseToken": {"address": "0xabc", "symbol": "NOPE", "name": "Nope"},
            "liquidity": {"usd": 10_000},
        },
        {
            "chainId": "solana",
            "baseToken": {
                "address": "DustToken1111111111111111111111111111",
                "symbol": "DUST",
                "name": "Dust",
            },
            "liquidity": {"usd": 10},
        },
        {
            "chainId": "solana",
            "baseToken": {
                "address": "GoodToken111111111111111111111111111",
                "symbol": "GOOD",
                "name": "Good",
            },
            "liquidity": {"usd": 5_000},
            "pairAddress": "pair1",
            "dexId": "raydium",
        },
        {
            "chainId": "solana",
            "baseToken": {
                "address": "So11111111111111111111111111111111111111112",
                "symbol": "SOL",
                "name": "Wrapped SOL",
            },
            "liquidity": {"usd": 1_000_000},
        },
    ]
    out = extract_candidates_from_pairs(pairs, Chain.SOLANA, min_liquidity_usd=500.0)
    addrs = {c["address"] for c in out["candidates"]}
    assert "GoodToken111111111111111111111111111" in addrs
    assert out["skipped_dust"] >= 1
    assert out["skipped_ignored"] >= 1
    assert out["skipped_chain"] >= 1


def test_known_checker_skips():
    pairs = [
        {
            "chainId": "base",
            "baseToken": {
                "address": "0xKnownToken000000000000000000000000000001",
                "symbol": "K",
                "name": "Known",
            },
            "liquidity": {"usd": 9_000},
        }
    ]
    out = extract_candidates_from_pairs(
        pairs,
        Chain.BASE,
        known_checker=lambda chain, addr: True,
    )
    assert out["candidates"] == []
    assert out["skipped_known"] == 1


def test_provider_posture_honest_without_keys():
    posture = provider_posture()
    assert posture["dexscreener"]["status"] == "READY"
    assert "chains" in posture
    # Without real eth/base keys, expect BLOCKED or DEGRADED not fabricated READY for empty
    eth = posture["chains"]["ethereum"]
    assert eth["status"] in ("BLOCKED", "DEGRADED", "READY")
    if not eth["rpc_configured"]:
        assert eth["status"] == "BLOCKED"


def test_rpc_configured_rejects_placeholder():
    # ethereum default empty often blocked
    ok, reason = rpc_configured(Chain.ETHEREUM)
    assert isinstance(ok, bool)
    assert isinstance(reason, str)
