"""Generic JSON-RPC providers for Solana, Ethereum, Base.

Requires RPC URLs / API keys from environment.
When credentials are missing the provider reports BLOCKED / DEGRADED
and returns structured errors — never fabricated chain data.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from meme_tracker.config import get_settings
from meme_tracker.models.enums import Chain
from meme_tracker.providers.base import (
    BaseProvider,
    ProviderCapability,
    ProviderResponse,
)


class JsonRpcProvider(BaseProvider):
    """Minimal JSON-RPC client used by chain adapters."""

    def __init__(
        self,
        name: str,
        chain: Chain,
        rpc_url: str,
        *,
        api_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 15.0,
    ):
        super().__init__()
        self.name = name
        self.supported_chains = [chain]
        self.capabilities = [
            ProviderCapability.RPC,
            ProviderCapability.TOKEN_METADATA,
            ProviderCapability.TRANSACTIONS,
        ]
        self.chain = chain
        self.rpc_url = rpc_url.rstrip("/")
        self.api_key = api_key
        self._client = client
        self._timeout = timeout
        self._owns_client = client is None
        self._request_id = 0

    def _configured(self) -> bool:
        if not self.rpc_url:
            return False
        # If URL ends with placeholder style, treat as missing
        if self.rpc_url.endswith("/") or "YOUR" in self.rpc_url.upper():
            return False
        return True

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def call(self, method: str, params: list[Any] | dict[str, Any] | None = None) -> ProviderResponse:
        if not self._configured():
            self.record_error("RPC URL / key not configured")
            return ProviderResponse(
                ok=False,
                source=self.name,
                error="RPC not configured — set the corresponding env var",
                limitations=["missing credentials"],
            )

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params if params is not None else [],
        }
        start = time.perf_counter()
        try:
            client = await self._get_client()
            headers = {"Content-Type": "application/json"}
            r = await client.post(self.rpc_url, json=payload, headers=headers)
            latency = (time.perf_counter() - start) * 1000
            body = r.json() if r.content else {}
            if r.status_code != 200 or "error" in body:
                err = body.get("error", {}).get("message") or f"HTTP {r.status_code}"
                self.record_error(err, degraded=r.status_code == 429)
                return ProviderResponse(
                    ok=False,
                    source=self.name,
                    error=err,
                    retryable=r.status_code in (429, 500, 502, 503),
                    latency_ms=latency,
                )
            self.record_success()
            return ProviderResponse(
                ok=True,
                source=self.name,
                data=body.get("result"),
                latency_ms=latency,
                source_timestamp=datetime.now(timezone.utc),
            )
        except httpx.TimeoutException:
            self.record_error("timeout", degraded=True)
            return ProviderResponse(ok=False, source=self.name, error="timeout", retryable=True)
        except Exception as e:
            self.record_error(str(e))
            return ProviderResponse(ok=False, source=self.name, error=str(e), retryable=True)

    async def ping(self) -> ProviderResponse:
        if self.chain == Chain.SOLANA:
            return await self.call("getHealth")
        # eth_blockNumber works for Ethereum & Base
        return await self.call("eth_blockNumber")


def build_solana_rpc() -> JsonRpcProvider:
    s = get_settings()
    url = s.solana_rpc_url
    if s.helius_api_key and "helius" not in url.lower():
        # Prefer Helius if key present and URL is public
        url = f"https://mainnet.helius-rpc.com/?api-key={s.helius_api_key}"
    return JsonRpcProvider("solana_rpc", Chain.SOLANA, url, api_key=s.helius_api_key)


def build_ethereum_rpc() -> JsonRpcProvider:
    s = get_settings()
    url = s.ethereum_rpc_url
    if s.alchemy_api_key and not url:
        url = f"https://eth-mainnet.g.alchemy.com/v2/{s.alchemy_api_key}"
    elif s.alchemy_api_key and url.rstrip("/").endswith("/v2"):
        url = f"{url.rstrip('/')}/{s.alchemy_api_key}"
    return JsonRpcProvider("ethereum_rpc", Chain.ETHEREUM, url or "", api_key=s.alchemy_api_key)


def build_base_rpc() -> JsonRpcProvider:
    s = get_settings()
    url = s.base_rpc_url
    if s.alchemy_api_key and not url:
        url = f"https://base-mainnet.g.alchemy.com/v2/{s.alchemy_api_key}"
    elif s.alchemy_api_key and url.rstrip("/").endswith("/v2"):
        url = f"{url.rstrip('/')}/{s.alchemy_api_key}"
    return JsonRpcProvider("base_rpc", Chain.BASE, url or "", api_key=s.alchemy_api_key)
