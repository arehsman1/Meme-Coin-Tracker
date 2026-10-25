"""Wallet Intelligence persistence.

Uses Supabase when configured and schema-ready; otherwise falls back to the
in-memory store so local/API tests work without credentials.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from meme_tracker.db.client import get_supabase, is_db_available, should_use_supabase
from meme_tracker.models.enums import Chain, WalletEventType, WalletSource
from meme_tracker.wallets.classification import DEFAULT_SIGNIFICANT_SWAP_USD, apply_significance
from meme_tracker.wallets.models import (
    TrackedWallet,
    TrackedWalletCreate,
    WalletProfile,
    WalletTransaction,
    WalletTransactionIn,
)
from meme_tracker.wallets.service import InMemoryWalletStore, get_wallet_store

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_tracked(row: dict[str, Any]) -> TrackedWallet:
    return TrackedWallet(
        id=row.get("id"),
        chain=Chain(row["chain"]),
        address=row["address"],
        label=row.get("label"),
        source=WalletSource(row.get("source") or "manual"),
        tags=list(row.get("tags") or []),
        is_active=bool(row.get("is_active", True)),
        significant_swap_usd_threshold=(
            float(row["significant_swap_usd_threshold"])
            if row.get("significant_swap_usd_threshold") is not None
            else None
        ),
        notes=row.get("notes"),
        metadata=dict(row.get("metadata") or {}),
        first_seen_at=row.get("first_seen_at"),
        last_activity_at=row.get("last_activity_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_tx(row: dict[str, Any]) -> WalletTransaction:
    from meme_tracker.models.enums import ValueUsdStatus

    raw_status = row.get("value_usd_status") or "UNKNOWN"
    try:
        usd_status = ValueUsdStatus(raw_status)
    except ValueError:
        usd_status = ValueUsdStatus.UNKNOWN

    return WalletTransaction(
        id=row.get("id"),
        chain=Chain(row["chain"]),
        wallet_address=row["wallet_address"],
        tx_signature=row["tx_signature"],
        log_index=row.get("log_index"),
        block_time=row.get("block_time"),
        event_type=WalletEventType(row["event_type"]),
        token_address=row.get("token_address"),
        token_id=row.get("token_id"),
        amount_raw=row.get("amount_raw"),
        amount_ui=float(row["amount_ui"]) if row.get("amount_ui") is not None else None,
        value_usd=float(row["value_usd"]) if row.get("value_usd") is not None else None,
        value_usd_status=usd_status,
        counterparty=row.get("counterparty"),
        dex_or_protocol=row.get("dex_or_protocol"),
        is_significant=bool(row.get("is_significant", False)),
        raw_provider_payload=dict(row.get("raw_provider_payload") or {}),
        provider=row.get("provider"),
        observed_at=row.get("observed_at"),
        created_at=row.get("created_at"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upsert_tracked_wallet(data: TrackedWalletCreate) -> TrackedWallet:
    if should_use_supabase():
        return _sb_upsert_tracked(data)
    return get_wallet_store().upsert_tracked(data)


def list_tracked_wallets(
    *,
    chain: Optional[str] = None,
    active_only: bool = True,
    limit: int = 100,
) -> list[TrackedWallet]:
    if should_use_supabase():
        return _sb_list_tracked(chain=chain, active_only=active_only, limit=limit)
    items = get_wallet_store().list_tracked(active_only=active_only)
    if chain:
        items = [w for w in items if w.chain.value == chain.lower()]
    return items[:limit]


def get_tracked_wallet(chain: str, address: str) -> Optional[TrackedWallet]:
    try:
        c = Chain(chain.lower())
    except ValueError:
        return None
    if should_use_supabase():
        return _sb_get_tracked(c, address)
    return get_wallet_store().get_tracked(c, address)


def deactivate_tracked_wallet(chain: str, address: str) -> bool:
    try:
        c = Chain(chain.lower())
    except ValueError:
        return False
    if should_use_supabase():
        return _sb_deactivate(c, address)
    return get_wallet_store().deactivate(c, address)


def ingest_wallet_transaction(
    tx: WalletTransactionIn,
    threshold_usd: float | None = None,
) -> Optional[WalletTransaction]:
    """Idempotent. Returns None on duplicate.

    On successful new insert, may enqueue a wallet activity alert
    (independent of token qualification status).
    """
    if should_use_supabase():
        record = _sb_ingest_tx(tx, threshold_usd=threshold_usd)
    else:
        record = get_wallet_store().ingest_transaction(tx, threshold_usd=threshold_usd)

    if record is not None:
        try:
            from meme_tracker.wallets.alerts import maybe_enqueue_wallet_alert

            maybe_enqueue_wallet_alert(record, threshold_usd=threshold_usd)
        except Exception as e:
            log.warning("wallet alert enqueue skipped: %s", e)

    return record


def list_wallet_transactions(
    *,
    chain: Optional[str] = None,
    wallet_address: Optional[str] = None,
    significant_only: bool = False,
    limit: int = 100,
) -> list[WalletTransaction]:
    if should_use_supabase():
        return _sb_list_tx(
            chain=chain,
            wallet_address=wallet_address,
            significant_only=significant_only,
            limit=limit,
        )
    c = Chain(chain.lower()) if chain else None
    return get_wallet_store().list_transactions(
        chain=c,
        wallet_address=wallet_address,
        significant_only=significant_only,
        limit=limit,
    )


def get_wallet_profile(chain: str, address: str) -> Optional[WalletProfile]:
    try:
        c = Chain(chain.lower())
    except ValueError:
        return None
    if should_use_supabase():
        return _sb_get_profile(c, address)
    return get_wallet_store().get_profile(c, address)


def wallet_storage_mode() -> str:
    if should_use_supabase():
        return "supabase"
    if is_db_available():
        return "supabase_degraded"
    return "memory"


# ---------------------------------------------------------------------------
# Supabase implementation
# ---------------------------------------------------------------------------


def _sb_upsert_tracked(data: TrackedWalletCreate) -> TrackedWallet:
    sb = get_supabase()
    assert sb is not None
    now = _utcnow().isoformat()
    payload = {
        "chain": data.chain.value,
        "address": data.address,
        "label": data.label,
        "source": data.source.value if hasattr(data.source, "value") else data.source,
        "tags": data.tags,
        "is_active": True,
        "significant_swap_usd_threshold": data.significant_swap_usd_threshold,
        "notes": data.notes,
        "metadata": data.metadata,
        "updated_at": now,
    }
    # Upsert on (chain, address)
    res = (
        sb.table("tracked_wallets")
        .upsert(payload, on_conflict="chain,address")
        .execute()
    )
    rows = res.data or []
    if not rows:
        # Fallback select
        sel = (
            sb.table("tracked_wallets")
            .select("*")
            .eq("chain", data.chain.value)
            .eq("address", data.address)
            .limit(1)
            .execute()
        )
        rows = sel.data or []
    if not rows:
        raise RuntimeError("tracked_wallets upsert returned no row")
    return _row_to_tracked(rows[0])


def _sb_list_tracked(
    *, chain: Optional[str], active_only: bool, limit: int
) -> list[TrackedWallet]:
    sb = get_supabase()
    assert sb is not None
    q = sb.table("tracked_wallets").select("*")
    if chain:
        q = q.eq("chain", chain.lower())
    if active_only:
        q = q.eq("is_active", True)
    q = q.order("last_activity_at", desc=True, nullsfirst=False).limit(limit)
    res = q.execute()
    return [_row_to_tracked(r) for r in (res.data or [])]


def _sb_get_tracked(chain: Chain, address: str) -> Optional[TrackedWallet]:
    sb = get_supabase()
    assert sb is not None
    res = (
        sb.table("tracked_wallets")
        .select("*")
        .eq("chain", chain.value)
        .eq("address", address)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return _row_to_tracked(rows[0]) if rows else None


def _sb_deactivate(chain: Chain, address: str) -> bool:
    sb = get_supabase()
    assert sb is not None
    res = (
        sb.table("tracked_wallets")
        .update({"is_active": False, "updated_at": _utcnow().isoformat()})
        .eq("chain", chain.value)
        .eq("address", address)
        .execute()
    )
    return bool(res.data)


def _sb_ingest_tx(
    tx: WalletTransactionIn, threshold_usd: float | None = None
) -> Optional[WalletTransaction]:
    sb = get_supabase()
    assert sb is not None

    thr = threshold_usd
    if thr is None:
        tracked = _sb_get_tracked(tx.chain, tx.wallet_address)
        if tracked and tracked.significant_swap_usd_threshold is not None:
            thr = float(tracked.significant_swap_usd_threshold)
        else:
            thr = DEFAULT_SIGNIFICANT_SWAP_USD

    enriched = apply_significance(tx, threshold_usd=thr)
    log_idx = enriched.log_index if enriched.log_index is not None else -1

    # Dedup check
    existing = (
        sb.table("wallet_transactions")
        .select("id")
        .eq("chain", enriched.chain.value)
        .eq("tx_signature", enriched.tx_signature)
        .eq("wallet_address", enriched.wallet_address)
        .eq("log_index", log_idx if enriched.log_index is not None else -1)
        .limit(1)
        .execute()
    )
    if existing.data:
        return None

    payload = {
        "id": str(uuid4()),
        "chain": enriched.chain.value,
        "wallet_address": enriched.wallet_address,
        "tx_signature": enriched.tx_signature,
        "log_index": enriched.log_index,
        "block_time": enriched.block_time.isoformat() if enriched.block_time else None,
        "event_type": enriched.event_type.value,
        "token_address": enriched.token_address,
        "token_id": str(enriched.token_id) if enriched.token_id else None,
        "amount_raw": enriched.amount_raw,
        "amount_ui": enriched.amount_ui,
        "value_usd": enriched.value_usd,
        "value_usd_status": (
            enriched.value_usd_status.value
            if hasattr(enriched.value_usd_status, "value")
            else enriched.value_usd_status
        ),
        "counterparty": enriched.counterparty,
        "dex_or_protocol": enriched.dex_or_protocol,
        "is_significant": enriched.is_significant,
        "raw_provider_payload": enriched.raw_provider_payload,
        "provider": enriched.provider,
        "observed_at": (enriched.observed_at or _utcnow()).isoformat(),
    }
    res = sb.table("wallet_transactions").insert(payload).execute()
    rows = res.data or []
    if not rows:
        return None

    # Touch last_activity on tracked wallet
    try:
        sb.table("tracked_wallets").update(
            {
                "last_activity_at": payload["block_time"] or payload["observed_at"],
                "updated_at": _utcnow().isoformat(),
            }
        ).eq("chain", enriched.chain.value).eq(
            "address", enriched.wallet_address
        ).execute()
    except Exception as e:
        log.warning("Could not update last_activity_at: %s", e)

    _sb_refresh_profile(enriched.chain, enriched.wallet_address)
    return _row_to_tx(rows[0])


def _sb_list_tx(
    *,
    chain: Optional[str],
    wallet_address: Optional[str],
    significant_only: bool,
    limit: int,
) -> list[WalletTransaction]:
    sb = get_supabase()
    assert sb is not None
    q = sb.table("wallet_transactions").select("*")
    if chain:
        q = q.eq("chain", chain.lower())
    if wallet_address:
        q = q.eq("wallet_address", wallet_address)
    if significant_only:
        q = q.eq("is_significant", True)
    q = q.order("block_time", desc=True, nullsfirst=False).limit(limit)
    res = q.execute()
    return [_row_to_tx(r) for r in (res.data or [])]


def _sb_refresh_profile(chain: Chain, address: str) -> None:
    """Recompute lightweight profile from transactions."""
    sb = get_supabase()
    if sb is None:
        return
    res = (
        sb.table("wallet_transactions")
        .select("event_type,is_significant,value_usd_status,block_time,created_at")
        .eq("chain", chain.value)
        .eq("wallet_address", address)
        .execute()
    )
    rows = res.data or []
    swaps = [r for r in rows if r.get("event_type") in ("SWAP_BUY", "SWAP_SELL")]
    significant = [r for r in rows if r.get("is_significant")]
    known = sum(1 for r in swaps if r.get("value_usd_status") == "KNOWN")
    completeness = int(100 * known / len(swaps)) if swaps else 0
    confidence = min(100, completeness + min(30, len(swaps) * 2))
    times = [
        r.get("block_time") or r.get("created_at")
        for r in rows
        if r.get("block_time") or r.get("created_at")
    ]
    first = min(times) if times else None
    last = max(times) if times else None
    payload = {
        "chain": chain.value,
        "address": address,
        "observed_trade_count": len(swaps),
        "observed_significant_count": len(significant),
        "first_observed_at": first,
        "last_observed_at": last,
        "data_completeness_score": completeness,
        "confidence_score": confidence,
        "updated_at": _utcnow().isoformat(),
    }
    try:
        sb.table("wallet_profiles").upsert(payload, on_conflict="chain,address").execute()
    except Exception as e:
        log.warning("wallet_profiles upsert failed: %s", e)


def _sb_get_profile(chain: Chain, address: str) -> Optional[WalletProfile]:
    sb = get_supabase()
    assert sb is not None
    res = (
        sb.table("wallet_profiles")
        .select("*")
        .eq("chain", chain.value)
        .eq("address", address)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return None
    r = rows[0]
    return WalletProfile(
        chain=Chain(r["chain"]),
        address=r["address"],
        display_name=r.get("display_name"),
        classification=list(r.get("classification") or []),
        observed_trade_count=int(r.get("observed_trade_count") or 0),
        observed_significant_count=int(r.get("observed_significant_count") or 0),
        first_observed_at=r.get("first_observed_at"),
        last_observed_at=r.get("last_observed_at"),
        data_completeness_score=int(r.get("data_completeness_score") or 0),
        confidence_score=int(r.get("confidence_score") or 0),
        notes=r.get("notes"),
        metadata=dict(r.get("metadata") or {}),
        updated_at=r.get("updated_at"),
    )
