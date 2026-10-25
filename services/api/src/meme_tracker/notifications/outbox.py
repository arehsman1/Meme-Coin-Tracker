"""Notification outbox helpers.

Design: write to notification_outbox first, then deliver.
A Telegram failure must not erase a qualification decision.
Deduplication via dedupe_key unique index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class OutboxMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    notification_type: str
    payload: dict[str, Any]
    recipient_telegram_id: Optional[int] = None
    dedupe_key: Optional[str] = None
    status: str = "pending"  # pending | sent | failed | deduplicated
    attempts: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def build_qualification_alert_payload(decision: dict[str, Any]) -> dict[str, Any]:
    """Build the structured payload for a NEW TOKEN / qualification alert."""
    return {
        "type": "qualification",
        "chain": decision.get("chain"),
        "address": decision.get("token_address"),
        "status": decision.get("final_status"),
        "name": decision.get("name"),
        "symbol": decision.get("symbol"),
        "scores": {
            "security_risk": decision.get("security_risk_score"),
            "manipulation_risk": decision.get("manipulation_risk_score"),
            "trade_quality": decision.get("trade_quality_score"),
            "data_confidence": decision.get("data_confidence_score"),
        },
        "reasons": decision.get("reasons", []),
        "evaluation_id": decision.get("evaluation_id"),
        "disclaimer": (
            "Assessment is not a guarantee of safety, sellability, profit or execution."
        ),
    }


def should_send_qualification_alert(final_status: str) -> bool:
    """Hard rule: only QUALIFIED_TRADE_CANDIDATE triggers the new-token alert."""
    return final_status == "QUALIFIED_TRADE_CANDIDATE"


def format_qualification_telegram_text(payload: dict[str, Any]) -> str:
    scores = payload.get("scores") or {}
    reasons = payload.get("reasons") or []
    lines = [
        "🆕 NEW TOKEN DETECTED",
        f"{payload.get('name') or '?'} ({payload.get('symbol') or '?'})",
        f"Chain: {payload.get('chain')}",
        f"Address: `{payload.get('address')}`",
        f"Status: {payload.get('status')}",
        f"Security risk: {scores.get('security_risk')}",
        f"Manipulation risk: {scores.get('manipulation_risk')}",
        f"Trade quality: {scores.get('trade_quality')}",
        f"Data confidence: {scores.get('data_confidence')}",
        "Reasons:",
    ]
    for r in reasons[:6]:
        lines.append(f"• {r}")
    lines.append("")
    lines.append(payload.get("disclaimer", ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wallet activity alerts (independent of token qualification)
# ---------------------------------------------------------------------------


def should_send_wallet_significant_alert(
    *,
    event_type: str,
    is_significant: bool,
    value_usd_status: str,
) -> bool:
    """
    Significant SWAP_BUY / SWAP_SELL with known USD value.
    Transfers and unknown valuations never trigger this alert.
    Independent of whether the related token is qualified or rejected.
    """
    if not is_significant:
        return False
    if value_usd_status == "UNKNOWN":
        return False
    return event_type in ("SWAP_BUY", "SWAP_SELL")


def build_wallet_significant_alert_payload(
    *,
    chain: str,
    wallet_address: str,
    event_type: str,
    tx_signature: str,
    value_usd: float | None,
    value_usd_status: str,
    token_address: str | None = None,
    token_qualification_status: str | None = None,
    wallet_label: str | None = None,
    dex_or_protocol: str | None = None,
    threshold_usd: float | None = None,
) -> dict[str, Any]:
    """Structured payload for a significant wallet swap alert."""
    side = "BUY" if event_type == "SWAP_BUY" else "SELL" if event_type == "SWAP_SELL" else event_type
    return {
        "type": "wallet_significant_swap",
        "chain": chain,
        "wallet_address": wallet_address,
        "wallet_label": wallet_label,
        "event_type": event_type,
        "side": side,
        "tx_signature": tx_signature,
        "token_address": token_address,
        "token_qualification_status": token_qualification_status,
        "value_usd": value_usd,
        "value_usd_status": value_usd_status,
        "threshold_usd": threshold_usd,
        "dex_or_protocol": dex_or_protocol,
        "disclaimer": (
            "Wallet activity alert — independent of token qualification. "
            "A rejected or un-qualified token may still appear here. "
            "Not a trade signal. Observed value may be incomplete."
        ),
    }


def format_wallet_significant_telegram_text(payload: dict[str, Any]) -> str:
    side = payload.get("side") or payload.get("event_type") or "?"
    emoji = "🟢" if side == "BUY" else "🔴" if side == "SELL" else "⚪"
    label = payload.get("wallet_label") or ""
    wallet_line = f"`{payload.get('wallet_address')}`"
    if label:
        wallet_line = f"{label} ({wallet_line})"

    lines = [
        f"{emoji} WALLET {side}",
        f"Chain: {payload.get('chain')}",
        f"Wallet: {wallet_line}",
    ]
    if payload.get("token_address"):
        lines.append(f"Token: `{payload.get('token_address')}`")
    status = payload.get("token_qualification_status")
    if status:
        lines.append(f"Token status: {status}")
    else:
        lines.append("Token status: unknown / not evaluated")
    if payload.get("value_usd") is not None:
        lines.append(f"Value: ~${payload.get('value_usd'):,.0f} ({payload.get('value_usd_status')})")
    if payload.get("threshold_usd") is not None:
        lines.append(f"Threshold: ${payload.get('threshold_usd'):,.0f}")
    if payload.get("dex_or_protocol"):
        lines.append(f"Venue: {payload.get('dex_or_protocol')}")
    if payload.get("tx_signature"):
        lines.append(f"Tx: `{payload.get('tx_signature')}`")
    lines.append("")
    lines.append(payload.get("disclaimer") or "")
    return "\n".join(lines)
