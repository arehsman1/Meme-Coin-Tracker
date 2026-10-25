"""DEX Screener adapter (public, no key required for basic endpoints).

Official base: https://api.dexscreener.com
We only use documented public endpoints. Rate limits apply.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from meme_tracker.config import get_settings
from meme_tracker.models.enums import Chain
from meme_tracker.providers.base import (
    BaseProvider,
    ProviderCapability,
    ProviderError,
    ProviderResponse,
)

# Chain → DexScreener chainId mapping
CHAIN_MAP = {
    Chain.SOLANA: "solana",
    Chain.ETHEREUM: "ethereum",
    Chain.BASE: "base",
}


class DexScreenerProvider(BaseProvider):
    name = "dexscreener"
    supported_chains = [Chain.SOLANA, Chain.ETHEREUM, Chain.BASE]
    capabilities = [
        ProviderCapability.PAIRS,
        ProviderCapability.LIQUIDITY,
        ProviderCapability.TOKEN_METADATA,
    ]

    BASE_URL = "https://api.dexscreener.com"

    def __init__(self, client: Optional[httpx.AsyncClient] = None, timeout: float = 12.0):
        super().__init__()
        self._client = client
        self._timeout = timeout
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> ProviderResponse:
        start = time.perf_counter()
        try:
            client = await self._get_client()
            # Lightweight public endpoint
            r = await client.get(f"{self.BASE_URL}/latest/dex/search", params={"q": "SOL"})
            latency = (time.perf_counter() - start) * 1000
            if r.status_code == 200:
                self.record_success()
                return ProviderResponse(
                    ok=True,
                    source=self.name,
                    data={"status": r.status_code},
                    latency_ms=latency,
                )
            self.record_error(f"HTTP {r.status_code}", degraded=True)
            return ProviderResponse(
                ok=False,
                source=self.name,
                error=f"HTTP {r.status_code}",
                retryable=r.status_code in (429, 500, 502, 503),
                latency_ms=latency,
            )
        except Exception as e:
            self.record_error(str(e))
            return ProviderResponse(
                ok=False,
                source=self.name,
                error=str(e),
                retryable=True,
            )

    async def get_token_pairs(self, chain: Chain, token_address: str) -> ProviderResponse:
        """Fetch pairs for a token. Returns UNKNOWN-style result on failure."""
        if chain not in CHAIN_MAP:
            return ProviderResponse(
                ok=False,
                source=self.name,
                error=f"Unsupported chain: {chain}",
                limitations=["chain not mapped"],
            )
        if not get_settings().dexscreener_enabled:
            return ProviderResponse(
                ok=False,
                source=self.name,
                error="DEXSCREENER_ENABLED=false",
                limitations=["provider disabled by config"],
            )

        chain_id = CHAIN_MAP[chain]
        start = time.perf_counter()
        try:
            client = await self._get_client()
            url = f"{self.BASE_URL}/token-pairs/v1/{chain_id}/{token_address}"
            r = await client.get(url)
            latency = (time.perf_counter() - start) * 1000

            if r.status_code == 200:
                self.record_success()
                data = r.json()
                # API may return list or object depending on version
                pairs = data if isinstance(data, list) else data.get("pairs") or data.get("pair") or []
                return ProviderResponse(
                    ok=True,
                    source=self.name,
                    data=pairs,
                    latency_ms=latency,
                    source_timestamp=datetime.now(timezone.utc),
                )
            if r.status_code == 404:
                self.record_success()
                return ProviderResponse(
                    ok=True,
                    source=self.name,
                    data=[],
                    latency_ms=latency,
                    limitations=["no pairs found (404)"],
                )
            self.record_error(f"HTTP {r.status_code}", degraded=r.status_code == 429)
            return ProviderResponse(
                ok=False,
                source=self.name,
                error=f"HTTP {r.status_code}",
                retryable=r.status_code in (429, 500, 502, 503),
                latency_ms=latency,
            )
        except httpx.TimeoutException:
            self.record_error("timeout", degraded=True)
            return ProviderResponse(
                ok=False, source=self.name, error="timeout", retryable=True
            )
        except Exception as e:
            self.record_error(str(e))
            return ProviderResponse(
                ok=False, source=self.name, error=str(e), retryable=True
            )

    async def search_pairs(self, query: str) -> ProviderResponse:
        start = time.perf_counter()
        try:
            client = await self._get_client()
            r = await client.get(
                f"{self.BASE_URL}/latest/dex/search", params={"q": query}
            )
            latency = (time.perf_counter() - start) * 1000
            if r.status_code == 200:
                self.record_success()
                body = r.json()
                return ProviderResponse(
                    ok=True,
                    source=self.name,
                    data=body.get("pairs", []),
                    latency_ms=latency,
                )
            return ProviderResponse(
                ok=False,
                source=self.name,
                error=f"HTTP {r.status_code}",
                retryable=r.status_code in (429, 500, 502, 503),
                latency_ms=latency,
            )
        except Exception as e:
            return ProviderResponse(ok=False, source=self.name, error=str(e), retryable=True)
