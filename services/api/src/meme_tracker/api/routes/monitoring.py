"""Monitoring / revocation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from meme_tracker.api.deps import require_env_admin
from meme_tracker.auth import Principal
from meme_tracker.monitoring.revocation import recheck_token, run_revocation_cycle

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


class RecheckBody(BaseModel):
    chain: str
    address: str


@router.post("/revocation/run")
async def run_revocation(principal: Principal = Depends(require_env_admin)):
    if not principal.has_permission("jobs.manage"):
        raise HTTPException(403, "Forbidden")
    return await run_revocation_cycle(limit=15)


@router.post("/revocation/recheck")
async def recheck_one(
    body: RecheckBody,
    principal: Principal = Depends(require_env_admin),
):
    if not principal.has_permission("tokens.evaluate"):
        raise HTTPException(403, "Forbidden")
    chain = body.chain.lower()
    if chain not in ("solana", "ethereum", "base"):
        raise HTTPException(400, "Unsupported chain")
    return await recheck_token(chain, body.address.strip())


@router.get("/status")
async def monitoring_status():
    """
    Operational snapshot for operators and Mini App service page.
    No secrets. Trading flags always reported as OFF at this surface.
    """
    from meme_tracker.outcomes import summarize_outcomes
    from meme_tracker.settings_public import build_public_settings
    from meme_tracker.worker.recovery import FailureTracker
    from meme_tracker.worker.wallet_jobs import list_cached_snapshots
    from meme_tracker.db import storage_mode, is_db_available

    # Import worker module state if available
    failure_stats = {"tracked_failures": 0, "exhausted": 0}
    try:
        from meme_tracker.worker import main as worker_main

        tracker = getattr(worker_main, "_job_failures", None)
        if isinstance(tracker, FailureTracker):
            failure_stats = tracker.stats()
    except Exception:
        pass

    snapshots = list_cached_snapshots(limit=20)
    outcomes = summarize_outcomes()
    public = build_public_settings()

    return {
        "storage": storage_mode(),
        "db_available": is_db_available(),
        "job_failure_tracker": failure_stats,
        "wallet_performance_snapshots_cached": len(snapshots),
        "outcomes": {
            "sample_size": outcomes.get("sample_size"),
            "complete": outcomes.get("complete"),
            "unavailable": outcomes.get("unavailable"),
            "immature": outcomes.get("immature"),
        },
        "safety": {
            "live_trading": public["live_trading"],
            "auto_trading_enabled": public["auto_trading_enabled"],
            "wallet_connection_enabled": public["wallet_connection_enabled"],
            "public_wallet_tracking_enabled": public["public_wallet_tracking_enabled"],
        },
        "note": "Cached wallet snapshots are process-local until Supabase persistence is applied.",
    }
