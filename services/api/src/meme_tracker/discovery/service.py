"""
New-token discovery.

Strategy (v0.1):
- Poll DexScreener search / token profiles for each chain.
- Extract unique token addresses from recent pairs.
- Skip addresses already in the store.
- Enqueue evaluate jobs for unknowns.

Does not scrape private endpoints. Respects rate limits via interval + single-flight.
Never marks a token qualified solely because it was discovered.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from meme_tracker.db import create_job, get_token, list_tokens
from meme_tracker.models.enums import Chain
from meme_tracker.providers.registry import get_registry

log = logging.getLogger(__name__)

# Well-known quote/base assets to ignore as "new meme" candidates
IGNORE_SYMBOLS = {
    "SOL", "WSOL", "ETH", "WETH", "USDC", "USDT", "DAI", "WBTC", "CBETH",
    "USD1", "USDbC", "cbBTC",
}
IGNORE_ADDRESSES = {
    # Solana
    "so11111111111111111111111111111111111111112",
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",  # USDC sol
    "es9vmfrsacqj9cg6asfdmwqljzoykzkia1vsxxcg2tmp",  # USDT sol
    # Ethereum
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    # Base
    "0x4200000000000000000000000000000000000006",  # WETH base
}


class DiscoveryResult:
    def __init__(self) -> None:
        self.scanned_pairs = 0
        self.candidates: list[dict[str, Any]] = []
        self.enqueued: list[str] = []
        self.skipped_known = 0
        self.skipped_ignored = 0
        self.errors: list[str] = []
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_pairs": self.scanned_pairs,
            "candidates": len(self.candidates),
            "enqueued": self.enqueued,
            "skipped_known": self.skipped_known,
            "skipped_ignored": self.skipped_ignored,
            "errors": self.errors,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def _norm_addr(address: str, chain: Chain) -> str:
    a = address.strip()
    if chain in (Chain.ETHEREUM, Chain.BASE):
        return a.lower()
    return a


def _is_ignored(address: str, symbol: Optional[str], chain: Chain) -> bool:
    if symbol and symbol.upper() in IGNORE_SYMBOLS:
        return True
    if _norm_addr(address, chain).lower() in IGNORE_ADDRESSES:
        return True
    return False


async def discover_from_dexscreener(
    chains: Optional[list[Chain]] = None,
    *,
    max_per_chain: int = 15,
    enqueue: bool = True,
) -> DiscoveryResult:
    """
    Pull recent pairs from DexScreener and enqueue evaluate jobs for new tokens.
    """
    result = DiscoveryResult()
    chains = chains or [Chain.SOLANA, Chain.ETHEREUM, Chain.BASE]
    registry = get_registry()
    dex = registry.get_dexscreener()
    if not dex:
        result.errors.append("DexScreener provider not registered")
        result.finished_at = datetime.now(timezone.utc)
        return result

    # Search queries that tend to surface active / new pairs per chain
    # DexScreener search is public; we use chain-oriented queries.
    queries = {
        Chain.SOLANA: ["solana", "raydium", "pump"],
        Chain.ETHEREUM: ["ethereum", "uniswap"],
        Chain.BASE: ["base", "aerodrome"],
    }

    seen: set[str] = set()

    for chain in chains:
        for q in queries.get(chain, [chain.value]):
            try:
                resp = await dex.search_pairs(q)
                if not resp.ok:
                    result.errors.append(f"{chain.value}/{q}: {resp.error}")
                    continue
                pairs = resp.data if isinstance(resp.data, list) else []
                result.scanned_pairs += len(pairs)

                extracted = extract_candidates_from_pairs(
                    pairs,
                    chain,
                    seen=seen,
                    known_checker=lambda ch, addr: bool(get_token(ch, addr)),
                    max_candidates=max_per_chain,
                )
                result.skipped_ignored += extracted["skipped_ignored"]
                result.skipped_known += extracted["skipped_known"]
                for candidate in extracted["candidates"]:
                    # Avoid double-counting if already added this cycle
                    already = sum(
                        1 for c in result.candidates if c["chain"] == chain.value
                    )
                    if already >= max_per_chain:
                        break
                    result.candidates.append(candidate)
                    if enqueue:
                        job_id = create_job(
                            "evaluate",
                            chain=chain.value,
                            token_address=candidate["address"],
                            payload={
                                "address": candidate["address"],
                                "source": "discovery_dexscreener",
                                "symbol": candidate.get("symbol"),
                            },
                        )
                        result.enqueued.append(job_id)
                        log.info(
                            "discovered %s %s (%s) job=%s liq=%s",
                            chain.value,
                            candidate.get("symbol"),
                            candidate["address"][:12],
                            job_id[:8],
                            candidate.get("liquidity_usd"),
                        )
            except Exception as e:
                result.errors.append(f"{chain.value}/{q}: {e}")
                log.exception("discovery error %s %s", chain.value, q)

            # small polite gap between queries
            time.sleep(0.15)

    result.finished_at = datetime.now(timezone.utc)
    return result


# Last discovery snapshot for API/status
_last_discovery: Optional[dict[str, Any]] = None


def get_last_discovery() -> Optional[dict[str, Any]]:
    return _last_discovery


async def run_discovery_cycle(
    chains: Optional[list[Chain]] = None,
    max_per_chain: int = 10,
) -> dict[str, Any]:
    global _last_discovery
    result = await discover_from_dexscreener(
        chains=chains, max_per_chain=max_per_chain, enqueue=True
    )
    _last_discovery = result.to_dict()
    log.info(
        "discovery cycle done pairs=%s candidates=%s enqueued=%s known=%s ignored=%s errors=%s",
        result.scanned_pairs,
        len(result.candidates),
        len(result.enqueued),
        result.skipped_known,
        result.skipped_ignored,
        len(result.errors),
    )
    return _last_discovery


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without network)
# ---------------------------------------------------------------------------

MIN_LIQUIDITY_USD_DEFAULT = 500.0


def extract_candidates_from_pairs(
    pairs: list[Any],
    chain: Chain,
    *,
    seen: set[str] | None = None,
    known_checker=None,
    max_candidates: int = 50,
    min_liquidity_usd: float | None = MIN_LIQUIDITY_USD_DEFAULT,
) -> dict[str, Any]:
    """
    Parse DexScreener-style pair objects into discovery candidates.
    known_checker(chain_str, address) -> truthy if already stored.
    Does not call the network or invent pairs.
    """
    seen = seen if seen is not None else set()
    candidates: list[dict[str, Any]] = []
    skipped_known = 0
    skipped_ignored = 0
    skipped_dust = 0
    skipped_chain = 0
    expected = {
        Chain.SOLANA: "solana",
        Chain.ETHEREUM: "ethereum",
        Chain.BASE: "base",
    }[chain]

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        chain_id = (pair.get("chainId") or "").lower()
        if chain_id and chain_id != expected:
            skipped_chain += 1
            continue

        base = pair.get("baseToken") or {}
        address = base.get("address")
        symbol = base.get("symbol")
        name = base.get("name")
        if not address:
            continue

        norm = _norm_addr(str(address), chain)
        key = f"{chain.value}:{norm}"
        if key in seen:
            continue
        seen.add(key)

        if _is_ignored(norm, symbol if isinstance(symbol, str) else None, chain):
            skipped_ignored += 1
            continue

        if known_checker and known_checker(chain.value, norm):
            skipped_known += 1
            continue

        liq = pair.get("liquidity") or {}
        usd = liq.get("usd") if isinstance(liq, dict) else None
        try:
            usd_f = float(usd) if usd is not None else None
        except (TypeError, ValueError):
            usd_f = None

        if min_liquidity_usd is not None and usd_f is not None and usd_f < min_liquidity_usd:
            skipped_dust += 1
            continue

        candidates.append(
            {
                "chain": chain.value,
                "address": norm,
                "symbol": symbol,
                "name": name,
                "liquidity_usd": usd_f,
                "pair_address": pair.get("pairAddress"),
                "dex": pair.get("dexId"),
                "pair_created_at": pair.get("pairCreatedAt"),
            }
        )
        if len(candidates) >= max_candidates:
            break

    return {
        "candidates": candidates,
        "skipped_known": skipped_known,
        "skipped_ignored": skipped_ignored,
        "skipped_dust": skipped_dust,
        "skipped_chain": skipped_chain,
        "seen": seen,
    }
