"""Token discovery and evaluation endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from meme_tracker.db import (
    get_token,
    list_evaluations_for_token,
    list_tokens,
    storage_mode,
    upsert_token_and_evaluation,
)
from meme_tracker.models.enums import Chain, QualificationStatus
from meme_tracker.qualification.pipeline import evaluate_token

router = APIRouter(prefix="/api/v1", tags=["tokens"])


class EvaluateRequest(BaseModel):
    chain: str
    address: str = Field(min_length=8, max_length=64)
    persist: bool = True


class EvaluateResponse(BaseModel):
    evaluation_id: str
    chain: str
    token_address: str
    final_status: str
    status_label: str
    security_risk_score: float
    manipulation_risk_score: float
    trade_quality_score: float
    data_confidence_score: float
    reasons: list[str]
    missing_evidence: list[str]
    policy_version: str
    engine_version: str
    evaluated_at: str
    name: Optional[str] = None
    symbol: Optional[str] = None
    persisted: bool = False
    storage: Optional[str] = None
    token_id: Optional[str] = None
    limitations_note: str = (
        "This assessment is not a guarantee of safety, sellability or profit. "
        "Critical checks may be UNKNOWN when provider data is incomplete."
    )


STATUS_LABELS = {
    QualificationStatus.REJECTED_CRITICAL_RISK: "REJECTED — CRITICAL RISK",
    QualificationStatus.PENDING_DATA_INCOMPLETE: "PENDING — DATA INCOMPLETE",
    QualificationStatus.MONITOR_NOT_YET_QUALIFIED: "MONITOR — NOT YET QUALIFIED",
    QualificationStatus.SECURITY_QUALIFIED_NO_TRADE_SIGNAL: "SECURITY QUALIFIED — NO TRADE SIGNAL",
    QualificationStatus.QUALIFIED_TRADE_CANDIDATE: "QUALIFIED — TRADE CANDIDATE",
    QualificationStatus.QUALIFICATION_REVOKED: "QUALIFICATION REVOKED",
}


@router.post("/tokens/evaluate", response_model=EvaluateResponse)
async def evaluate(body: EvaluateRequest):
    """
    Collect evidence, run qualification engine, optionally persist.
    Rate-limit in production — hits external providers.
    """
    try:
        chain = Chain(body.chain.lower())
    except ValueError:
        raise HTTPException(400, "Unsupported chain. Use: solana, ethereum, base")

    try:
        decision = await evaluate_token(chain, body.address.strip())
    except Exception as e:
        raise HTTPException(502, f"Evaluation failed: {e}") from e

    persisted = False
    storage = None
    token_id = None
    if body.persist:
        try:
            result = upsert_token_and_evaluation(decision)
            persisted = True
            storage = result.get("storage")
            token_id = result.get("token_id")
        except Exception as e:
            storage = f"error:{e}"

    return EvaluateResponse(
        evaluation_id=decision.evaluation_id,
        chain=decision.chain.value,
        token_address=decision.token_address,
        final_status=decision.final_status.value,
        status_label=STATUS_LABELS.get(decision.final_status, decision.final_status.value),
        security_risk_score=decision.scores.security_risk_score,
        manipulation_risk_score=decision.scores.manipulation_risk_score,
        trade_quality_score=decision.scores.trade_quality_score,
        data_confidence_score=decision.scores.data_confidence_score,
        reasons=decision.reasons,
        missing_evidence=decision.missing_evidence,
        policy_version=decision.policy_version,
        engine_version=decision.engine_version,
        evaluated_at=decision.evaluated_at.isoformat(),
        name=decision.identity.name,
        symbol=decision.identity.symbol,
        persisted=persisted,
        storage=storage,
        token_id=token_id,
    )


@router.get("/tokens/{chain}/{address}")
async def get_token_endpoint(chain: str, address: str):
    try:
        c = Chain(chain.lower())
    except ValueError:
        raise HTTPException(400, "Unsupported chain")

    stored = get_token(c.value, address.strip())
    if stored:
        evals = list_evaluations_for_token(c.value, address.strip(), limit=5)
        return {
            "source": "store",
            "storage": storage_mode(),
            "token": stored,
            "recent_evaluations": evals,
        }

    decision = await evaluate_token(c, address.strip())
    return {
        "source": "live",
        "storage": storage_mode(),
        "chain": decision.chain.value,
        "address": decision.token_address,
        "name": decision.identity.name,
        "symbol": decision.identity.symbol,
        "current_status": decision.final_status.value,
        "status_label": STATUS_LABELS.get(decision.final_status),
        "scores": decision.scores.model_dump(),
        "reasons": decision.reasons,
        "evaluated_at": decision.evaluated_at.isoformat(),
        "note": "Not found in store — returned live evaluation (not persisted)",
    }


@router.get("/tokens/{chain}/{address}/evaluations")
async def token_evaluations(chain: str, address: str, limit: int = Query(10, ge=1, le=50)):
    try:
        Chain(chain.lower())
    except ValueError:
        raise HTTPException(400, "Unsupported chain")
    items = list_evaluations_for_token(chain.lower(), address.strip(), limit=limit)
    return {"items": items, "storage": storage_mode()}


@router.get("/tokens")
async def list_tokens_endpoint(
    status: Optional[str] = Query(None),
    chain: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    items = list_tokens(status=status, chain=chain, limit=limit)
    return {
        "items": items,
        "total": len(items),
        "limit": limit,
        "storage": storage_mode(),
    }


class EnqueueRequest(BaseModel):
    chain: str
    address: str = Field(min_length=8, max_length=64)


@router.post("/jobs/evaluate")
async def enqueue_evaluate_job(body: EnqueueRequest):
    """Enqueue an evaluate job for the worker (async path)."""
    try:
        Chain(body.chain.lower())
    except ValueError:
        raise HTTPException(400, "Unsupported chain")
    from meme_tracker.db import create_job

    job_id = create_job(
        "evaluate",
        chain=body.chain.lower(),
        token_address=body.address.strip(),
        payload={"address": body.address.strip()},
    )
    return {"job_id": job_id, "status": "pending", "storage": storage_mode()}
