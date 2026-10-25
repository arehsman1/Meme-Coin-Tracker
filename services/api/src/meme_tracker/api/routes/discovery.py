"""Discovery and outcome API endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from meme_tracker.db import storage_mode
from meme_tracker.discovery import get_last_discovery, run_discovery_cycle
from meme_tracker.models.enums import Chain
from meme_tracker.outcomes import list_outcomes

router = APIRouter(prefix="/api/v1", tags=["discovery"])


class DiscoveryRunRequest(BaseModel):
    chains: Optional[list[str]] = None
    max_per_chain: int = Field(default=8, ge=1, le=30)


@router.post("/discovery/run")
async def trigger_discovery(body: DiscoveryRunRequest | None = None):
    """
    Manually trigger a discovery cycle.
    Enqueues evaluate jobs for new token addresses found via DexScreener.
    Rate-limit this in production.
    """
    body = body or DiscoveryRunRequest()
    chains = None
    if body.chains:
        try:
            chains = [Chain(c.lower()) for c in body.chains]
        except ValueError:
            raise HTTPException(400, "Invalid chain in list")
    summary = await run_discovery_cycle(chains=chains, max_per_chain=body.max_per_chain)
    return {"ok": True, "storage": storage_mode(), **summary}


@router.get("/discovery/last")
async def last_discovery():
    last = get_last_discovery()
    return {
        "last": last,
        "storage": storage_mode(),
        "note": "null until a discovery cycle has run",
    }


@router.get("/outcomes")
async def outcomes(
    evaluation_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    items = list_outcomes(evaluation_id=evaluation_id, limit=limit)
    return {"items": items, "storage": storage_mode()}
