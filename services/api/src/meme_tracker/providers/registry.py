"""Central provider registry used by scanners and health endpoints."""

from __future__ import annotations

from typing import Optional

from meme_tracker.models.enums import Chain
from meme_tracker.providers.base import BaseProvider
from meme_tracker.providers.dexscreener import DexScreenerProvider
from meme_tracker.providers.rpc import (
    JsonRpcProvider,
    build_base_rpc,
    build_ethereum_rpc,
    build_solana_rpc,
)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._rpc: dict[Chain, JsonRpcProvider] = {}
        self._dex: Optional[DexScreenerProvider] = None

    def register_defaults(self) -> None:
        self._rpc[Chain.SOLANA] = build_solana_rpc()
        self._rpc[Chain.ETHEREUM] = build_ethereum_rpc()
        self._rpc[Chain.BASE] = build_base_rpc()
        for p in self._rpc.values():
            self._providers[p.name] = p

        self._dex = DexScreenerProvider()
        self._providers[self._dex.name] = self._dex

    def get_rpc(self, chain: Chain) -> Optional[JsonRpcProvider]:
        return self._rpc.get(chain)

    def get_dexscreener(self) -> Optional[DexScreenerProvider]:
        return self._dex

    def all(self) -> list[BaseProvider]:
        return list(self._providers.values())

    def health_report(self) -> list[dict]:
        return [p.health_snapshot() for p in self.all()]

    async def close_all(self) -> None:
        for p in self.all():
            close = getattr(p, "close", None)
            if close:
                await close()


# Module-level singleton for simple use
_registry: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.register_defaults()
    return _registry
