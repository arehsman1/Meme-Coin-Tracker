"""Evidence models for qualification pipeline."""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict

from .enums import (
    CheckResult,
    Chain,
    SellabilityStatus,
    RelationshipConfidence,
    QualificationStatus,
)


class EvidenceItem(BaseModel):
    """Single piece of evidence with provenance."""

    model_config = ConfigDict(extra="forbid")

    key: str
    value: Any
    source: str
    source_timestamp: Optional[datetime] = None
    observed_at: datetime
    retrieval_status: str = "ok"  # ok | timeout | error | rate_limited | missing
    freshness_threshold_seconds: Optional[int] = None
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    limitations: list[str] = Field(default_factory=list)
    conflicting_values: list[Any] = Field(default_factory=list)


class CheckOutcome(BaseModel):
    """Result of one mandatory or optional check."""

    model_config = ConfigDict(extra="forbid")

    check_id: str
    name: str
    result: CheckResult
    severity: str = "normal"  # critical | high | normal | low
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    is_required: bool = True


class ScoreComponent(BaseModel):
    name: str
    weight: float
    raw_value: float
    normalized: float
    contribution: float


class ScoreSet(BaseModel):
    security_risk_score: float = Field(ge=0, le=100)
    manipulation_risk_score: float = Field(ge=0, le=100)
    trade_quality_score: float = Field(ge=0, le=100)
    data_confidence_score: float = Field(ge=0, le=100)
    components: list[ScoreComponent] = Field(default_factory=list)


class TokenIdentity(BaseModel):
    chain: Chain
    address: str  # canonical mint / contract address
    name: Optional[str] = None
    symbol: Optional[str] = None
    decimals: Optional[int] = None
    creation_time: Optional[datetime] = None
    first_trade_time: Optional[datetime] = None
    metadata_source: Optional[str] = None
    identity_conflicts: list[str] = Field(default_factory=list)


class LiquiditySnapshot(BaseModel):
    pool_address: Optional[str] = None
    dex: Optional[str] = None
    base_reserve: Optional[float] = None
    quote_reserve: Optional[float] = None
    quote_symbol: Optional[str] = None
    liquidity_usd: Optional[float] = None
    locked: Optional[bool] = None
    lock_until: Optional[datetime] = None
    observed_at: datetime
    source: str


class SellabilityEvidence(BaseModel):
    status: SellabilityStatus
    method: str  # observed_tx | simulation | quote | none
    price_impact_pct: Optional[float] = None
    route_available: Optional[bool] = None
    limitations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class HolderSnapshot(BaseModel):
    top_holder_pct: Optional[float] = None
    top10_pct: Optional[float] = None
    creator_pct: Optional[float] = None
    unique_holders: Optional[int] = None
    observed_at: datetime
    source: str
    excluded_addresses: list[str] = Field(default_factory=list)


class WalletRelationship(BaseModel):
    wallet_a: str
    wallet_b: str
    relationship_type: str
    confidence: RelationshipConfidence
    evidence: list[str] = Field(default_factory=list)


class QualificationDecision(BaseModel):
    """Authoritative output of the qualification engine."""

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    revision: int = 1
    chain: Chain
    token_address: str
    policy_version: str
    engine_version: str
    identity: TokenIdentity
    checks: list[CheckOutcome]
    scores: ScoreSet
    final_status: QualificationStatus
    reasons: list[str]
    missing_evidence: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    data_freshness_summary: dict[str, Any] = Field(default_factory=dict)
    sellability: Optional[SellabilityEvidence] = None
    liquidity: list[LiquiditySnapshot] = Field(default_factory=list)
    holders: Optional[HolderSnapshot] = None
    evaluated_at: datetime
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
