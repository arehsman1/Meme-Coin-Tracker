"""Wallet Intelligence domain models (Pydantic)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from meme_tracker.models.enums import (
    Chain,
    ValueUsdStatus,
    WalletEventType,
    WalletSource,
)


class TrackedWalletCreate(BaseModel):
    chain: Chain
    address: str
    label: str | None = None
    source: WalletSource = WalletSource.MANUAL
    tags: list[str] = Field(default_factory=list)
    significant_swap_usd_threshold: float | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("address")
    @classmethod
    def normalise_address(cls, v: str, info) -> str:
        v = v.strip()
        if not v:
            raise ValueError("address required")
        # Solana: keep original case. EVM: lowercase for storage consistency.
        chain = info.data.get("chain")
        if chain in (Chain.ETHEREUM, Chain.BASE) or (
            isinstance(chain, str) and chain in ("ethereum", "base")
        ):
            if v.startswith("0x"):
                return v.lower()
        return v


class TrackedWallet(TrackedWalletCreate):
    id: UUID | None = None
    is_active: bool = True
    first_seen_at: datetime | None = None
    last_activity_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WalletTransactionIn(BaseModel):
    chain: Chain
    wallet_address: str
    tx_signature: str
    log_index: int | None = None
    block_time: datetime | None = None
    event_type: WalletEventType
    token_address: str | None = None
    token_id: UUID | None = None
    amount_raw: str | None = None
    amount_ui: float | None = None
    value_usd: float | None = None
    value_usd_status: ValueUsdStatus = ValueUsdStatus.UNKNOWN
    counterparty: str | None = None
    dex_or_protocol: str | None = None
    is_significant: bool = False
    raw_provider_payload: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    observed_at: datetime | None = None


class WalletTransaction(WalletTransactionIn):
    id: UUID | None = None
    created_at: datetime | None = None


class WalletProfile(BaseModel):
    chain: Chain
    address: str
    display_name: str | None = None
    classification: list[str] = Field(default_factory=list)
    observed_trade_count: int = 0
    observed_significant_count: int = 0
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    data_completeness_score: int = 0
    confidence_score: int = 0
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class SignificantSwapDecision(BaseModel):
    """Result of applying the configurable significant-swap threshold."""
    is_significant: bool
    value_usd: float | None
    value_usd_status: ValueUsdStatus
    threshold_used: float
    reason: str
