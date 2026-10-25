"""Persistence for tokens, evaluations, status history, outbox, scanner jobs.

Uses Supabase when configured; otherwise an in-process memory store so
local evaluate → list flows work without credentials.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from meme_tracker.db.client import get_supabase, is_db_available, should_use_supabase
from meme_tracker.models.evidence import QualificationDecision
from meme_tracker.models.enums import Chain, QualificationStatus
from meme_tracker.notifications.outbox import (
    OutboxMessage,
    build_qualification_alert_payload,
    should_send_qualification_alert,
)

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_mem_tokens: dict[str, dict[str, Any]] = {}  # key = f"{chain}:{address}"
_mem_evals: dict[str, dict[str, Any]] = {}  # key = evaluation_id
_mem_history: list[dict[str, Any]] = []
_mem_outbox: list[dict[str, Any]] = []
_mem_jobs: list[dict[str, Any]] = []


def _token_key(chain: str, address: str) -> str:
    return f"{chain.lower()}:{address}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upsert_token_and_evaluation(decision: QualificationDecision) -> dict[str, Any]:
    """
    Persist token + evaluation + check results + status history.
    Enqueue qualification alert if status is QUALIFIED_TRADE_CANDIDATE.
    Returns a summary dict with token_id and evaluation_id.
    """
    if should_use_supabase():
        return _supabase_persist(decision)
    return _memory_persist(decision)


def get_token(chain: str, address: str) -> Optional[dict[str, Any]]:
    if should_use_supabase():
        return _supabase_get_token(chain, address)
    key = _token_key(chain, address)
    with _lock:
        return _mem_tokens.get(key)


def list_tokens(
    *,
    status: Optional[str] = None,
    chain: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if should_use_supabase():
        return _supabase_list_tokens(status=status, chain=chain, limit=limit)
    with _lock:
        items = list(_mem_tokens.values())
    if chain:
        items = [t for t in items if t.get("chain") == chain.lower()]
    if status:
        items = [t for t in items if t.get("current_status") == status]
    items.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
    return items[:limit]


def get_evaluation(evaluation_id: str) -> Optional[dict[str, Any]]:
    if should_use_supabase():
        return _supabase_get_evaluation(evaluation_id)
    with _lock:
        return _mem_evals.get(evaluation_id)


def list_evaluations_for_token(chain: str, address: str, limit: int = 10) -> list[dict[str, Any]]:
    token = get_token(chain, address)
    if not token:
        return []
    token_id = token.get("id")
    if should_use_supabase():
        return _supabase_list_evals(token_id, limit)
    with _lock:
        items = [e for e in _mem_evals.values() if e.get("token_id") == token_id]
    items.sort(key=lambda e: e.get("evaluated_at") or "", reverse=True)
    return items[:limit]


def enqueue_outbox(msg: OutboxMessage) -> str:
    if should_use_supabase():
        return _supabase_enqueue_outbox(msg)
    with _lock:
        row = msg.model_dump(mode="json")
        # dedupe
        if msg.dedupe_key:
            for existing in _mem_outbox:
                if existing.get("dedupe_key") == msg.dedupe_key:
                    existing["status"] = "deduplicated"
                    return existing["id"]
        _mem_outbox.append(row)
        return msg.id


def claim_pending_outbox(limit: int = 10) -> list[dict[str, Any]]:
    if should_use_supabase():
        return _supabase_claim_outbox(limit)
    with _lock:
        pending = [m for m in _mem_outbox if m.get("status") == "pending"]
        claimed = pending[:limit]
        for m in claimed:
            m["status"] = "sending"
            m["attempts"] = m.get("attempts", 0) + 1
        return [dict(m) for m in claimed]


def mark_outbox_sent(outbox_id: str, success: bool, error: Optional[str] = None) -> None:
    if should_use_supabase():
        _supabase_mark_outbox(outbox_id, success, error)
        return
    with _lock:
        for m in _mem_outbox:
            if m.get("id") == outbox_id:
                m["status"] = "sent" if success else "failed"
                m["last_error"] = error
                if success:
                    m["sent_at"] = _utcnow().isoformat()
                break


def create_job(
    job_type: str,
    *,
    chain: Optional[str] = None,
    token_address: Optional[str] = None,
    payload: Optional[dict] = None,
) -> str:
    job_id = str(uuid4())
    row = {
        "id": job_id,
        "job_type": job_type,
        "chain": chain,
        "token_address": token_address,
        "payload": payload or {},
        "status": "pending",
        "created_at": _utcnow().isoformat(),
        "lease_owner": None,
        "lease_expires_at": None,
    }
    if should_use_supabase():
        return _supabase_create_job(row)
    with _lock:
        _mem_jobs.append(row)
    return job_id


def claim_jobs(worker_id: str, limit: int = 5, lease_seconds: int = 120) -> list[dict[str, Any]]:
    """Lease pending jobs to a worker. Prevents duplicate execution."""
    if should_use_supabase():
        return _supabase_claim_jobs(worker_id, limit, lease_seconds)
    now = _utcnow()
    with _lock:
        claimed = []
        for job in _mem_jobs:
            if job["status"] not in ("pending", "running"):
                continue
            exp = job.get("lease_expires_at")
            if job["status"] == "running" and exp:
                try:
                    if datetime.fromisoformat(exp.replace("Z", "+00:00")) > now:
                        continue  # still leased
                except Exception:
                    pass
            job["status"] = "running"
            job["lease_owner"] = worker_id
            job["lease_expires_at"] = (
                now.replace(microsecond=0).isoformat()
            )  # simplified
            job["started_at"] = now.isoformat()
            claimed.append(dict(job))
            if len(claimed) >= limit:
                break
        return claimed


def complete_job(job_id: str, *, error: Optional[str] = None) -> None:
    if should_use_supabase():
        _supabase_complete_job(job_id, error)
        return
    with _lock:
        for job in _mem_jobs:
            if job["id"] == job_id:
                job["status"] = "failed" if error else "completed"
                job["error"] = error
                job["finished_at"] = _utcnow().isoformat()
                job["lease_owner"] = None
                break


def storage_mode() -> str:
    if should_use_supabase():
        return "supabase"
    if is_db_available():
        return "supabase_degraded"
    return "memory"


# ---------------------------------------------------------------------------
# Memory implementation
# ---------------------------------------------------------------------------


def _memory_persist(decision: QualificationDecision) -> dict[str, Any]:
    key = _token_key(decision.chain.value, decision.token_address)
    with _lock:
        existing = _mem_tokens.get(key)
        token_id = existing["id"] if existing else str(uuid4())
        prev_status = existing.get("current_status") if existing else None
        revision = 1
        if existing and existing.get("current_evaluation_id"):
            prev_eval = _mem_evals.get(existing["current_evaluation_id"])
            if prev_eval:
                revision = int(prev_eval.get("revision", 1)) + 1

        token_row = {
            "id": token_id,
            "chain": decision.chain.value,
            "address": decision.token_address,
            "name": decision.identity.name,
            "symbol": decision.identity.symbol,
            "decimals": decision.identity.decimals,
            "current_status": decision.final_status.value,
            "current_evaluation_id": decision.evaluation_id,
            "discovered_at": existing["discovered_at"] if existing else decision.evaluated_at.isoformat(),
            "updated_at": decision.evaluated_at.isoformat(),
        }
        _mem_tokens[key] = token_row

        eval_row = {
            "id": decision.evaluation_id,
            "token_id": token_id,
            "revision": revision,
            "policy_version": decision.policy_version,
            "engine_version": decision.engine_version,
            "final_status": decision.final_status.value,
            "security_risk_score": decision.scores.security_risk_score,
            "manipulation_risk_score": decision.scores.manipulation_risk_score,
            "trade_quality_score": decision.scores.trade_quality_score,
            "data_confidence_score": decision.scores.data_confidence_score,
            "reasons": decision.reasons,
            "missing_evidence": decision.missing_evidence,
            "conflicting_evidence": decision.conflicting_evidence,
            "evaluated_at": decision.evaluated_at.isoformat(),
            "checks": [c.model_dump(mode="json") for c in decision.checks],
            "name": decision.identity.name,
            "symbol": decision.identity.symbol,
            "chain": decision.chain.value,
            "token_address": decision.token_address,
        }
        _mem_evals[decision.evaluation_id] = eval_row

        _mem_history.append(
            {
                "id": str(uuid4()),
                "token_id": token_id,
                "evaluation_id": decision.evaluation_id,
                "from_status": prev_status,
                "to_status": decision.final_status.value,
                "reason": "; ".join(decision.reasons[:3]),
                "changed_at": decision.evaluated_at.isoformat(),
            }
        )

    # Outbox — only for qualified trade candidates, and only on status transition into it
    if should_send_qualification_alert(decision.final_status.value):
        if prev_status != QualificationStatus.QUALIFIED_TRADE_CANDIDATE.value:
            payload = build_qualification_alert_payload(
                {
                    "chain": decision.chain.value,
                    "token_address": decision.token_address,
                    "final_status": decision.final_status.value,
                    "name": decision.identity.name,
                    "symbol": decision.identity.symbol,
                    "security_risk_score": decision.scores.security_risk_score,
                    "manipulation_risk_score": decision.scores.manipulation_risk_score,
                    "trade_quality_score": decision.scores.trade_quality_score,
                    "data_confidence_score": decision.scores.data_confidence_score,
                    "reasons": decision.reasons,
                    "evaluation_id": decision.evaluation_id,
                }
            )
            dedupe = f"qual:{decision.chain.value}:{decision.token_address}:{decision.evaluation_id}"
            enqueue_outbox(
                OutboxMessage(
                    notification_type="qualification",
                    payload=payload,
                    dedupe_key=dedupe,
                )
            )

    # Schedule historical outcome horizons (non-blocking structure)
    try:
        from meme_tracker.outcomes.collector import schedule_outcome_jobs
        schedule_outcome_jobs(
            decision.evaluation_id,
            decision.chain.value,
            decision.token_address,
        )
    except Exception as e:
        log.debug("outcome schedule skipped: %s", e)

    return {
        "token_id": token_id,
        "evaluation_id": decision.evaluation_id,
        "revision": revision,
        "storage": "memory",
        "previous_status": prev_status,
        "current_status": decision.final_status.value,
    }


# ---------------------------------------------------------------------------
# Supabase implementation (best-effort; schema must be migrated)
# ---------------------------------------------------------------------------


def _supabase_persist(decision: QualificationDecision) -> dict[str, Any]:
    sb = get_supabase()
    assert sb is not None
    chain = decision.chain.value
    address = decision.token_address

    # Upsert token
    existing = (
        sb.table("tokens")
        .select("id,current_status,current_evaluation_id")
        .eq("chain", chain)
        .eq("address", address)
        .limit(1)
        .execute()
    )
    prev_status = None
    token_id = None
    revision = 1
    if existing.data:
        row = existing.data[0]
        token_id = row["id"]
        prev_status = row.get("current_status")
        # next revision
        last = (
            sb.table("qualification_evaluations")
            .select("revision")
            .eq("token_id", token_id)
            .order("revision", desc=True)
            .limit(1)
            .execute()
        )
        if last.data:
            revision = int(last.data[0]["revision"]) + 1
        sb.table("tokens").update(
            {
                "name": decision.identity.name,
                "symbol": decision.identity.symbol,
                "decimals": decision.identity.decimals,
                "current_status": decision.final_status.value,
                "current_evaluation_id": decision.evaluation_id,
                "updated_at": decision.evaluated_at.isoformat(),
            }
        ).eq("id", token_id).execute()
    else:
        token_id = str(uuid4())
        sb.table("tokens").insert(
            {
                "id": token_id,
                "chain": chain,
                "address": address,
                "name": decision.identity.name,
                "symbol": decision.identity.symbol,
                "decimals": decision.identity.decimals,
                "current_status": decision.final_status.value,
                "current_evaluation_id": decision.evaluation_id,
                "discovered_at": decision.evaluated_at.isoformat(),
                "updated_at": decision.evaluated_at.isoformat(),
            }
        ).execute()

    # Evaluation (use decision.evaluation_id as PK)
    sb.table("qualification_evaluations").insert(
        {
            "id": decision.evaluation_id,
            "token_id": token_id,
            "revision": revision,
            "policy_version": decision.policy_version,
            "engine_version": decision.engine_version,
            "final_status": decision.final_status.value,
            "security_risk_score": decision.scores.security_risk_score,
            "manipulation_risk_score": decision.scores.manipulation_risk_score,
            "trade_quality_score": decision.scores.trade_quality_score,
            "data_confidence_score": decision.scores.data_confidence_score,
            "reasons": decision.reasons,
            "missing_evidence": decision.missing_evidence,
            "conflicting_evidence": decision.conflicting_evidence,
            "data_freshness": decision.data_freshness_summary,
            "evaluated_at": decision.evaluated_at.isoformat(),
        }
    ).execute()

    # Check results
    check_rows = [
        {
            "evaluation_id": decision.evaluation_id,
            "check_id": c.check_id,
            "name": c.name,
            "result": c.result.value,
            "severity": c.severity,
            "reason": c.reason,
            "is_required": c.is_required,
            "evidence_refs": c.evidence_refs,
        }
        for c in decision.checks
    ]
    if check_rows:
        sb.table("qualification_check_results").insert(check_rows).execute()

    # Score components
    score_rows = [
        {
            "evaluation_id": decision.evaluation_id,
            "name": s.name,
            "weight": s.weight,
            "raw_value": s.raw_value,
            "normalized": s.normalized,
            "contribution": s.contribution,
        }
        for s in decision.scores.components
    ]
    if score_rows:
        sb.table("qualification_score_components").insert(score_rows).execute()

    # Status history
    sb.table("qualification_status_history").insert(
        {
            "token_id": token_id,
            "evaluation_id": decision.evaluation_id,
            "from_status": prev_status,
            "to_status": decision.final_status.value,
            "reason": "; ".join(decision.reasons[:3]),
        }
    ).execute()

    # Outbox
    if should_send_qualification_alert(decision.final_status.value):
        if prev_status != QualificationStatus.QUALIFIED_TRADE_CANDIDATE.value:
            payload = build_qualification_alert_payload(
                {
                    "chain": chain,
                    "token_address": address,
                    "final_status": decision.final_status.value,
                    "name": decision.identity.name,
                    "symbol": decision.identity.symbol,
                    "security_risk_score": decision.scores.security_risk_score,
                    "manipulation_risk_score": decision.scores.manipulation_risk_score,
                    "trade_quality_score": decision.scores.trade_quality_score,
                    "data_confidence_score": decision.scores.data_confidence_score,
                    "reasons": decision.reasons,
                    "evaluation_id": decision.evaluation_id,
                }
            )
            dedupe = f"qual:{chain}:{address}:{decision.evaluation_id}"
            enqueue_outbox(
                OutboxMessage(
                    notification_type="qualification",
                    payload=payload,
                    dedupe_key=dedupe,
                )
            )

    return {
        "token_id": token_id,
        "evaluation_id": decision.evaluation_id,
        "revision": revision,
        "storage": "supabase",
        "previous_status": prev_status,
        "current_status": decision.final_status.value,
    }


def _supabase_get_token(chain: str, address: str) -> Optional[dict[str, Any]]:
    sb = get_supabase()
    assert sb is not None
    r = (
        sb.table("tokens")
        .select("*")
        .eq("chain", chain.lower())
        .eq("address", address)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def _supabase_list_tokens(
    *, status: Optional[str], chain: Optional[str], limit: int
) -> list[dict[str, Any]]:
    sb = get_supabase()
    assert sb is not None
    q = sb.table("tokens").select("*").order("updated_at", desc=True).limit(limit)
    if chain:
        q = q.eq("chain", chain.lower())
    if status:
        q = q.eq("current_status", status)
    r = q.execute()
    return r.data or []


def _supabase_get_evaluation(evaluation_id: str) -> Optional[dict[str, Any]]:
    sb = get_supabase()
    assert sb is not None
    r = (
        sb.table("qualification_evaluations")
        .select("*")
        .eq("id", evaluation_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def _supabase_list_evals(token_id: str, limit: int) -> list[dict[str, Any]]:
    sb = get_supabase()
    assert sb is not None
    r = (
        sb.table("qualification_evaluations")
        .select("*")
        .eq("token_id", token_id)
        .order("evaluated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return r.data or []


def _supabase_enqueue_outbox(msg: OutboxMessage) -> str:
    sb = get_supabase()
    assert sb is not None
    row = {
        "id": msg.id,
        "notification_type": msg.notification_type,
        "payload": msg.payload,
        "recipient_telegram_id": msg.recipient_telegram_id,
        "status": "pending",
        "dedupe_key": msg.dedupe_key,
        "attempts": 0,
    }
    try:
        sb.table("notification_outbox").insert(row).execute()
    except Exception as e:
        # Unique violation on dedupe_key → treat as deduplicated
        log.info("outbox insert skipped (likely dedupe): %s", e)
    return msg.id


def _supabase_claim_outbox(limit: int) -> list[dict[str, Any]]:
    sb = get_supabase()
    assert sb is not None
    r = (
        sb.table("notification_outbox")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    rows = r.data or []
    for row in rows:
        sb.table("notification_outbox").update(
            {"status": "sending", "attempts": (row.get("attempts") or 0) + 1}
        ).eq("id", row["id"]).eq("status", "pending").execute()
    return rows


def _supabase_mark_outbox(outbox_id: str, success: bool, error: Optional[str]) -> None:
    sb = get_supabase()
    assert sb is not None
    update: dict[str, Any] = {
        "status": "sent" if success else "failed",
        "last_error": error,
    }
    if success:
        update["sent_at"] = _utcnow().isoformat()
    sb.table("notification_outbox").update(update).eq("id", outbox_id).execute()


def _supabase_create_job(row: dict[str, Any]) -> str:
    sb = get_supabase()
    assert sb is not None
    sb.table("scanner_job_runs").insert(
        {
            "id": row["id"],
            "job_type": row["job_type"],
            "chain": row.get("chain"),
            "status": "pending",
        }
    ).execute()
    return row["id"]


def _supabase_claim_jobs(worker_id: str, limit: int, lease_seconds: int) -> list[dict[str, Any]]:
    sb = get_supabase()
    assert sb is not None
    # Simple claim: pending jobs
    r = (
        sb.table("scanner_job_runs")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    rows = r.data or []
    claimed = []
    for row in rows:
        upd = (
            sb.table("scanner_job_runs")
            .update(
                {
                    "status": "running",
                    "lease_owner": worker_id,
                    "started_at": _utcnow().isoformat(),
                }
            )
            .eq("id", row["id"])
            .eq("status", "pending")
            .execute()
        )
        if upd.data:
            claimed.append(upd.data[0] if isinstance(upd.data, list) else row)
    return claimed


def _supabase_complete_job(job_id: str, error: Optional[str]) -> None:
    sb = get_supabase()
    assert sb is not None
    sb.table("scanner_job_runs").update(
        {
            "status": "failed" if error else "completed",
            "error": error,
            "finished_at": _utcnow().isoformat(),
            "lease_owner": None,
        }
    ).eq("id", job_id).execute()
