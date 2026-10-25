"""
Continuous qualification revocation.

Re-evaluates tokens that currently hold elevated statuses and, when a
material risk appears (critical FAIL or sellability FAILED), persists a new
evaluation with QUALIFICATION_REVOKED and enqueues a risk alert.

Does not auto-trade. Does not fabricate risk.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from meme_tracker.db import enqueue_outbox, list_tokens, upsert_token_and_evaluation
from meme_tracker.models.enums import Chain, CheckResult, QualificationStatus, SellabilityStatus
from meme_tracker.models.evidence import QualificationDecision
from meme_tracker.notifications.outbox import OutboxMessage
from meme_tracker.qualification.pipeline import evaluate_token

log = logging.getLogger(__name__)

# Statuses that are candidates for continuous monitoring
MONITOR_STATUSES = (
    QualificationStatus.QUALIFIED_TRADE_CANDIDATE.value,
    QualificationStatus.SECURITY_QUALIFIED_NO_TRADE_SIGNAL.value,
    QualificationStatus.MONITOR_NOT_YET_QUALIFIED.value,
)

CRITICAL_CHECK_IDS = frozenset(
    {
        "identity_valid",
        "mint_authority_renounced_or_safe",
        "freeze_authority_safe",
        "no_blacklist_or_pause_trap",
        "sellability_not_failed",
        "liquidity_present",
    }
)


def material_risk_reasons(decision: QualificationDecision) -> list[str]:
    """Return reasons that justify revocation; empty if no material risk."""
    reasons: list[str] = []
    for c in decision.checks:
        if c.result == CheckResult.FAIL and c.check_id in CRITICAL_CHECK_IDS:
            reasons.append(f"CRITICAL FAIL: {c.name} — {c.reason}")
    if decision.sellability and decision.sellability.status == SellabilityStatus.FAILED:
        reasons.append("Sellability FAILED on re-evaluation")
    if decision.final_status == QualificationStatus.REJECTED_CRITICAL_RISK:
        # Engine already classified as rejected — treat as material for prior qualified tokens
        for r in decision.reasons:
            if r not in reasons:
                reasons.append(r)
    return reasons


def should_revoke(
    previous_status: Optional[str],
    decision: QualificationDecision,
) -> bool:
    """
    Revoke only if the token was in a non-rejected monitoring state and
    material risk is now present.
    """
    if previous_status not in MONITOR_STATUSES:
        return False
    if previous_status == QualificationStatus.QUALIFICATION_REVOKED.value:
        return False
    return bool(material_risk_reasons(decision))


def build_revocation_decision(
    decision: QualificationDecision,
    risk_reasons: list[str],
) -> QualificationDecision:
    """Clone decision with forced QUALIFICATION_REVOKED status and merged reasons."""
    reasons = list(risk_reasons)
    reasons.append("Qualification revoked after continuous risk re-evaluation")
    return decision.model_copy(
        update={
            "evaluation_id": str(uuid4()),
            "revision": decision.revision + 1,
            "final_status": QualificationStatus.QUALIFICATION_REVOKED,
            "reasons": reasons,
            "evaluated_at": datetime.now(timezone.utc),
        }
    )


def build_revocation_alert_payload(decision: QualificationDecision) -> dict[str, Any]:
    return {
        "type": "revocation",
        "chain": decision.chain.value,
        "address": decision.token_address,
        "status": decision.final_status.value,
        "name": decision.identity.name if decision.identity else None,
        "symbol": decision.identity.symbol if decision.identity else None,
        "reasons": decision.reasons,
        "evaluation_id": decision.evaluation_id,
        "scores": {
            "security_risk": decision.scores.security_risk_score,
            "manipulation_risk": decision.scores.manipulation_risk_score,
            "trade_quality": decision.scores.trade_quality_score,
            "data_confidence": decision.scores.data_confidence_score,
        },
        "disclaimer": (
            "Revocation is based on re-evaluated evidence. "
            "Not a guarantee of past or future market outcomes."
        ),
    }


def format_revocation_telegram_text(payload: dict[str, Any]) -> str:
    lines = [
        "⚠️ QUALIFICATION REVOKED",
        f"{payload.get('name') or '?'} ({payload.get('symbol') or '?'})",
        f"Chain: {payload.get('chain')}",
        f"Address: `{payload.get('address')}`",
        "Reasons:",
    ]
    for r in (payload.get("reasons") or [])[:8]:
        lines.append(f"• {r}")
    lines.append("")
    lines.append(payload.get("disclaimer") or "")
    return "\n".join(lines)


def enqueue_revocation_alert(decision: QualificationDecision) -> Optional[str]:
    payload = build_revocation_alert_payload(decision)
    msg = OutboxMessage(
        notification_type="risk_revocation",
        payload=payload,
        dedupe_key=f"revoke:{decision.chain.value}:{decision.token_address}:{decision.evaluation_id}",
    )
    return enqueue_outbox(msg)


async def recheck_token(chain: str, address: str) -> dict[str, Any]:
    """
    Re-evaluate one token. If material risk vs prior status, persist revocation.
    Returns a summary dict.
    """
    prior = None
    try:
        from meme_tracker.db import get_token

        prior = get_token(chain, address)
    except Exception:
        prior = None

    previous_status = (prior or {}).get("current_status")
    chain_e = Chain(chain.lower())
    decision = await evaluate_token(chain_e, address)

    summary: dict[str, Any] = {
        "chain": chain,
        "address": address,
        "previous_status": previous_status,
        "new_status": decision.final_status.value,
        "revoked": False,
        "evaluation_id": decision.evaluation_id,
    }

    if should_revoke(previous_status, decision):
        risk = material_risk_reasons(decision)
        revoked = build_revocation_decision(decision, risk)
        persist = upsert_token_and_evaluation(revoked)
        enqueue_revocation_alert(revoked)
        summary["revoked"] = True
        summary["new_status"] = revoked.final_status.value
        summary["evaluation_id"] = revoked.evaluation_id
        summary["reasons"] = revoked.reasons
        summary["persisted"] = persist
        log.warning(
            "REVOKED %s:%s prev=%s reasons=%s",
            chain,
            address[:12],
            previous_status,
            risk[:2],
        )
    else:
        # Still persist the fresh evaluation for audit trail
        persist = upsert_token_and_evaluation(decision)
        summary["persisted"] = persist

    return summary


async def run_revocation_cycle(*, limit: int = 15) -> dict[str, Any]:
    """
    Re-check up to `limit` tokens in monitoring statuses.
    """
    candidates: list[dict[str, Any]] = []
    for status in MONITOR_STATUSES:
        batch = list_tokens(status=status, limit=limit)
        candidates.extend(batch)
        if len(candidates) >= limit:
            break
    candidates = candidates[:limit]

    results = []
    revoked_count = 0
    for t in candidates:
        chain = t.get("chain")
        address = t.get("address")
        if not chain or not address:
            continue
        try:
            summary = await recheck_token(chain, address)
            results.append(summary)
            if summary.get("revoked"):
                revoked_count += 1
        except Exception as e:
            log.error("revocation recheck failed %s:%s %s", chain, address, e)
            results.append(
                {
                    "chain": chain,
                    "address": address,
                    "error": str(e),
                    "revoked": False,
                }
            )

    return {
        "checked": len(results),
        "revoked": revoked_count,
        "results": results,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
