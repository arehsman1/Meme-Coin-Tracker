"""Shared types and helpers for chain-specific evidence collection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from meme_tracker.models.enums import Chain, CheckResult
from meme_tracker.models.evidence import (
    CheckOutcome,
    EvidenceItem,
    HolderSnapshot,
    LiquiditySnapshot,
    SellabilityEvidence,
    TokenIdentity,
)
from meme_tracker.providers.registry import ProviderRegistry


class CollectedEvidence(BaseModel):
    """Everything a chain adapter returns for one token evaluation."""

    identity: TokenIdentity
    checks: list[CheckOutcome] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    sellability: Optional[SellabilityEvidence] = None
    liquidity: list[LiquiditySnapshot] = Field(default_factory=list)
    holders: Optional[HolderSnapshot] = None
    limitations: list[str] = Field(default_factory=list)


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_check(
    check_id: str,
    name: str,
    result: CheckResult,
    reason: str,
    *,
    severity: str = "normal",
    is_required: bool = True,
    evidence_refs: Optional[list[str]] = None,
) -> CheckOutcome:
    return CheckOutcome(
        check_id=check_id,
        name=name,
        result=result,
        severity=severity,
        reason=reason,
        is_required=is_required,
        evidence_refs=evidence_refs or [],
    )


class ChainAdapter(ABC):
    chain: Chain

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

    @abstractmethod
    async def collect(self, address: str) -> CollectedEvidence:
        """Collect identity, checks, liquidity, holders, sellability signals."""
        ...

    def _validate_address_format(self, address: str) -> bool:
        """Override per chain. Default accepts non-empty strings."""
        return bool(address and len(address) >= 8)
