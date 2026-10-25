"""Canonical enums shared across the system."""

from enum import Enum


class Chain(str, Enum):
    SOLANA = "solana"
    ETHEREUM = "ethereum"
    BASE = "base"


class CheckResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class QualificationStatus(str, Enum):
    REJECTED_CRITICAL_RISK = "REJECTED_CRITICAL_RISK"
    PENDING_DATA_INCOMPLETE = "PENDING_DATA_INCOMPLETE"
    MONITOR_NOT_YET_QUALIFIED = "MONITOR_NOT_YET_QUALIFIED"
    SECURITY_QUALIFIED_NO_TRADE_SIGNAL = "SECURITY_QUALIFIED_NO_TRADE_SIGNAL"
    QUALIFIED_TRADE_CANDIDATE = "QUALIFIED_TRADE_CANDIDATE"
    QUALIFICATION_REVOKED = "QUALIFICATION_REVOKED"


class SellabilityStatus(str, Enum):
    VERIFIED = "VERIFIED"
    LIMITED = "LIMITED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class RelationshipConfidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    POSSIBLE = "POSSIBLE"
    UNKNOWN = "UNKNOWN"


class ServiceDesiredState(str, Enum):
    ON = "ON"
    OFF = "OFF"


class ServiceActualState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    STARTING = "STARTING"
    STOPPING = "STOPPING"


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class WalletEventType(str, Enum):
    """Explicit classification — never treat every transfer as a buy/sell."""
    SWAP_BUY = "SWAP_BUY"
    SWAP_SELL = "SWAP_SELL"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    LIQUIDITY_ADD = "LIQUIDITY_ADD"
    LIQUIDITY_REMOVE = "LIQUIDITY_REMOVE"
    CONTRACT_INTERACTION = "CONTRACT_INTERACTION"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class WalletSource(str, Enum):
    MANUAL = "manual"
    CREATOR = "creator"
    EARLY_BUYER = "early_buyer"
    SIGNIFICANT_BUYER = "significant_buyer"
    SIGNIFICANT_SELLER = "significant_seller"
    DISCOVERED = "discovered"
    RULE = "rule"
    IMPORT = "import"


class ValueUsdStatus(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    ESTIMATED = "ESTIMATED"
