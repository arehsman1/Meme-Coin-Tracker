"""Wallet Intelligence API — public tracking independent of personal wallet connection."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from meme_tracker.api.deps import require_permission
from meme_tracker.auth import Principal
from meme_tracker.models.enums import Chain, ValueUsdStatus, WalletEventType, WalletSource
from meme_tracker.wallets.models import TrackedWalletCreate, WalletTransactionIn
from meme_tracker.wallets.repository import (
    deactivate_tracked_wallet,
    get_tracked_wallet,
    get_wallet_profile,
    ingest_wallet_transaction,
    list_tracked_wallets,
    list_wallet_transactions,
    upsert_tracked_wallet,
    wallet_storage_mode,
)

router = APIRouter(prefix="/api/v1/wallets", tags=["wallets"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TrackWalletRequest(BaseModel):
    chain: str
    address: str = Field(min_length=8, max_length=128)
    label: Optional[str] = None
    source: str = "manual"
    tags: list[str] = Field(default_factory=list)
    significant_swap_usd_threshold: Optional[float] = None
    notes: Optional[str] = None


class IngestTxRequest(BaseModel):
    chain: str
    wallet_address: str
    tx_signature: str
    log_index: Optional[int] = None
    block_time: Optional[str] = None
    event_type: str
    token_address: Optional[str] = None
    amount_raw: Optional[str] = None
    amount_ui: Optional[float] = None
    value_usd: Optional[float] = None
    value_usd_status: str = "UNKNOWN"
    counterparty: Optional[str] = None
    dex_or_protocol: Optional[str] = None
    provider: Optional[str] = None


class WalletSummary(BaseModel):
    chain: str
    address: str
    label: Optional[str] = None
    source: str
    tags: list[str] = []
    is_active: bool = True
    significant_swap_usd_threshold: Optional[float] = None
    last_activity_at: Optional[str] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_wallets(
    chain: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
):
    """List tracked public wallets. Read-only for anonymous; full for authorised."""
    items = list_tracked_wallets(chain=chain, active_only=active_only, limit=limit)
    return {
        "storage": wallet_storage_mode(),
        "count": len(items),
        "wallets": [
            {
                "id": str(w.id) if w.id else None,
                "chain": w.chain.value,
                "address": w.address,
                "label": w.label,
                "source": w.source.value if hasattr(w.source, "value") else w.source,
                "tags": w.tags,
                "is_active": w.is_active,
                "significant_swap_usd_threshold": w.significant_swap_usd_threshold,
                "last_activity_at": (
                    w.last_activity_at.isoformat() if w.last_activity_at else None
                ),
                "notes": w.notes,
            }
            for w in items
        ],
    }


@router.post("/track")
async def track_wallet(
    body: TrackWalletRequest,
    principal: Principal = Depends(require_permission("wallets.manage")),
):
    """Add or re-activate a public wallet for tracking."""
    try:
        chain = Chain(body.chain.lower())
    except ValueError:
        raise HTTPException(400, "Unsupported chain. Use: solana, ethereum, base")

    try:
        source = WalletSource(body.source.lower())
    except ValueError:
        source = WalletSource.MANUAL

    try:
        wallet = upsert_tracked_wallet(
            TrackedWalletCreate(
                chain=chain,
                address=body.address.strip(),
                label=body.label,
                source=source,
                tags=body.tags,
                significant_swap_usd_threshold=body.significant_swap_usd_threshold,
                notes=body.notes,
            )
        )
    except Exception as e:
        raise HTTPException(502, f"Failed to track wallet: {e}") from e

    return {
        "ok": True,
        "storage": wallet_storage_mode(),
        "wallet": {
            "id": str(wallet.id) if wallet.id else None,
            "chain": wallet.chain.value,
            "address": wallet.address,
            "label": wallet.label,
            "source": wallet.source.value if hasattr(wallet.source, "value") else wallet.source,
            "is_active": wallet.is_active,
        },
    }


@router.post("/untrack")
async def untrack_wallet(
    chain: str = Query(...),
    address: str = Query(..., min_length=8),
    principal: Principal = Depends(require_permission("wallets.manage")),
):
    """Deactivate tracking (does not delete history)."""
    ok = deactivate_tracked_wallet(chain, address.strip())
    if not ok:
        raise HTTPException(404, "Wallet not found or already inactive")
    return {"ok": True, "storage": wallet_storage_mode()}


@router.get("/{chain}/{address}")
async def get_wallet(chain: str, address: str):
    """Wallet profile + recent activity summary."""
    tracked = get_tracked_wallet(chain, address.strip())
    profile = get_wallet_profile(chain, address.strip())
    recent = list_wallet_transactions(
        chain=chain, wallet_address=address.strip(), limit=20
    )
    if not tracked and not profile and not recent:
        raise HTTPException(404, "Wallet not found")

    return {
        "storage": wallet_storage_mode(),
        "tracked": (
            {
                "chain": tracked.chain.value,
                "address": tracked.address,
                "label": tracked.label,
                "source": tracked.source.value if hasattr(tracked.source, "value") else tracked.source,
                "tags": tracked.tags,
                "is_active": tracked.is_active,
                "significant_swap_usd_threshold": tracked.significant_swap_usd_threshold,
                "last_activity_at": (
                    tracked.last_activity_at.isoformat() if tracked.last_activity_at else None
                ),
                "notes": tracked.notes,
            }
            if tracked
            else None
        ),
        "profile": (
            {
                "observed_trade_count": profile.observed_trade_count,
                "observed_significant_count": profile.observed_significant_count,
                "data_completeness_score": profile.data_completeness_score,
                "confidence_score": profile.confidence_score,
                "first_observed_at": (
                    profile.first_observed_at.isoformat()
                    if profile.first_observed_at
                    else None
                ),
                "last_observed_at": (
                    profile.last_observed_at.isoformat() if profile.last_observed_at else None
                ),
            }
            if profile
            else None
        ),
        "recent_transactions": [
            {
                "tx_signature": t.tx_signature,
                "event_type": t.event_type.value if hasattr(t.event_type, "value") else t.event_type,
                "token_address": t.token_address,
                "value_usd": t.value_usd,
                "value_usd_status": (
                    t.value_usd_status.value
                    if hasattr(t.value_usd_status, "value")
                    else t.value_usd_status
                ),
                "is_significant": t.is_significant,
                "block_time": t.block_time.isoformat() if t.block_time else None,
                "dex_or_protocol": t.dex_or_protocol,
            }
            for t in recent
        ],
        "note": (
            "Public wallet tracking does not require personal wallet connection. "
            "Performance figures reflect only observed history and may be incomplete."
        ),
    }


@router.get("/{chain}/{address}/transactions")
async def get_wallet_transactions(
    chain: str,
    address: str,
    significant_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    items = list_wallet_transactions(
        chain=chain,
        wallet_address=address.strip(),
        significant_only=significant_only,
        limit=limit,
    )
    return {
        "storage": wallet_storage_mode(),
        "count": len(items),
        "transactions": [
            {
                "id": str(t.id) if t.id else None,
                "tx_signature": t.tx_signature,
                "event_type": t.event_type.value if hasattr(t.event_type, "value") else t.event_type,
                "token_address": t.token_address,
                "amount_ui": t.amount_ui,
                "value_usd": t.value_usd,
                "value_usd_status": (
                    t.value_usd_status.value
                    if hasattr(t.value_usd_status, "value")
                    else t.value_usd_status
                ),
                "is_significant": t.is_significant,
                "block_time": t.block_time.isoformat() if t.block_time else None,
                "counterparty": t.counterparty,
                "dex_or_protocol": t.dex_or_protocol,
                "provider": t.provider,
            }
            for t in items
        ],
    }


@router.post("/transactions/ingest")
async def ingest_transaction(
    body: IngestTxRequest,
    principal: Principal = Depends(require_permission("wallets.manage")),
):
    """
    Ingest a classified wallet event (from scanner/worker).
    Idempotent on (chain, tx_signature, log_index, wallet_address).
    """
    try:
        chain = Chain(body.chain.lower())
    except ValueError:
        raise HTTPException(400, "Unsupported chain")

    try:
        event_type = WalletEventType(body.event_type.upper())
    except ValueError:
        raise HTTPException(400, f"Invalid event_type: {body.event_type}")

    try:
        usd_status = ValueUsdStatus(body.value_usd_status.upper())
    except ValueError:
        usd_status = ValueUsdStatus.UNKNOWN

    from datetime import datetime

    block_time = None
    if body.block_time:
        try:
            block_time = datetime.fromisoformat(body.block_time.replace("Z", "+00:00"))
        except ValueError:
            pass

    # EVM address normalisation
    wallet_addr = body.wallet_address.strip()
    if chain in (Chain.ETHEREUM, Chain.BASE) and wallet_addr.startswith("0x"):
        wallet_addr = wallet_addr.lower()

    tx = WalletTransactionIn(
        chain=chain,
        wallet_address=wallet_addr,
        tx_signature=body.tx_signature.strip(),
        log_index=body.log_index,
        block_time=block_time,
        event_type=event_type,
        token_address=body.token_address,
        amount_raw=body.amount_raw,
        amount_ui=body.amount_ui,
        value_usd=body.value_usd,
        value_usd_status=usd_status,
        counterparty=body.counterparty,
        dex_or_protocol=body.dex_or_protocol,
        provider=body.provider,
    )

    record = ingest_wallet_transaction(tx)
    if record is None:
        return {
            "ok": True,
            "deduplicated": True,
            "storage": wallet_storage_mode(),
        }

    return {
        "ok": True,
        "deduplicated": False,
        "storage": wallet_storage_mode(),
        "transaction": {
            "id": str(record.id) if record.id else None,
            "event_type": record.event_type.value,
            "is_significant": record.is_significant,
            "value_usd": record.value_usd,
            "value_usd_status": (
                record.value_usd_status.value
                if hasattr(record.value_usd_status, "value")
                else record.value_usd_status
            ),
        },
    }


@router.get("/{chain}/{address}/performance")
async def get_wallet_performance(chain: str, address: str):
    """
    Observed performance from SWAP events only.
    Includes sample size, completeness, confidence, and limitations.
    Does not invent PnL for unknown USD or treat transfers as trades.
    Optionally annotates the latest worker-cached snapshot if present.
    """
    try:
        Chain(chain.lower())
    except ValueError:
        raise HTTPException(400, "Unsupported chain")

    addr = address.strip()
    txs = list_wallet_transactions(
        chain=chain, wallet_address=addr, limit=500
    )
    from meme_tracker.wallets.ledger import compute_performance, snapshot_to_dict
    from meme_tracker.worker.wallet_jobs import get_latest_snapshot

    snap = compute_performance(chain.lower(), addr, txs)
    cached = get_latest_snapshot(chain.lower(), addr)
    return {
        "storage": wallet_storage_mode(),
        "performance": snapshot_to_dict(snap),
        "worker_cached_snapshot": cached,
        "note": (
            "Live computation from stored transactions. "
            "worker_cached_snapshot is process-local until durable persistence is applied."
        ),
    }
