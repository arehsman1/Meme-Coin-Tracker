"""Provider interfaces and common types.

All real network calls go through concrete adapters.
Stubs return structured UNKNOWN / error results — never fabricated success.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from meme_tracker.models.enums import Chain, HealthState


class ProviderCapability(str, Enum):
    RPC = "rpc"
    TOKEN_METADATA = "token_metadata"
    HOLDERS = "holders"
    LIQUIDITY = "liquidity"
    PAIRS = "pairs"
    TRANSACTIONS = "transactions"
    SIMULATION = "simulation"
    WEBHOOKS = "webhooks"


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status_code: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class ProviderResponse(BaseModel):
    """Normalized wrapper around any provider call."""

    ok: bool
    source: str
    data: Any = None
    error: Optional[str] = None
    retryable: bool = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_timestamp: Optional[datetime] = None
    latency_ms: Optional[float] = None
    limitations: list[str] = Field(default_factory=list)


class BaseProvider(ABC):
    name: str
    supported_chains: list[Chain]
    capabilities: list[ProviderCapability]

    def __init__(self) -> None:
        self._health = HealthState.UNKNOWN
        self._last_success: Optional[datetime] = None
        self._last_error: Optional[str] = None

    @property
    def health(self) -> HealthState:
        return self._health

    def record_success(self) -> None:
        self._health = HealthState.HEALTHY
        self._last_success = datetime.now(timezone.utc)
        self._last_error = None

    def record_error(self, message: str, *, degraded: bool = False) -> None:
        self._health = HealthState.DEGRADED if degraded else HealthState.UNHEALTHY
        self._last_error = message

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "health": self._health.value,
            "last_success": self._last_success.isoformat() if self._last_success else None,
            "last_error": self._last_error,
            "capabilities": [c.value for c in self.capabilities],
            "chains": [c.value for c in self.supported_chains],
        }

    @abstractmethod
    async def ping(self) -> ProviderResponse:
        """Lightweight health check."""
        ...
