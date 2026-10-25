"""Wallet-related background jobs (performance snapshots).

Computes observed performance from stored transactions only.
Does not invent history or mark incomplete samples as complete.
"""

from __future__ import annotations

import logging
from typing import Any

from meme_tracker.wallets.ledger import compute_performance, snapshot_to_dict
from meme_tracker.wallets.repository import (
    list_tracked_wallets,
    list_wallet_transactions,
    wallet_storage_mode,
)

log = logging.getLogger("worker.wallet_jobs")

# In-memory latest snapshots when Supabase persistence is not wired
_latest_snapshots: dict[str, dict[str, Any]] = {}


def snapshot_key(chain: str, address: str) -> str:
    return f"{chain.lower()}:{address}"


def get_latest_snapshot(chain: str, address: str) -> dict[str, Any] | None:
    return _latest_snapshots.get(snapshot_key(chain, address))


def list_cached_snapshots(limit: int = 50) -> list[dict[str, Any]]:
    return list(_latest_snapshots.values())[:limit]


def clear_snapshot_cache() -> None:
    _latest_snapshots.clear()


def run_wallet_performance_cycle(*, limit_wallets: int = 50, tx_limit: int = 500) -> dict[str, Any]:
    """
    For each active tracked wallet, recompute performance from observed txs.
    Returns summary counts — never claims complete lifetime P&L.
    """
    wallets = list_tracked_wallets(active_only=True, limit=limit_wallets)
    computed = 0
    empty = 0
    errors = 0

    for w in wallets:
        chain = getattr(w.chain, "value", str(w.chain))
        address = w.address
        try:
            txs = list_wallet_transactions(
                chain=chain, wallet_address=address, limit=tx_limit
            )
            snap = compute_performance(chain, address, txs)
            payload = snapshot_to_dict(snap)
            payload["storage"] = wallet_storage_mode()
            _latest_snapshots[snapshot_key(chain, address)] = payload
            computed += 1
            if snap.sample_size == 0:
                empty += 1
        except Exception as e:
            errors += 1
            log.warning("performance cycle failed %s:%s — %s", chain, address[:8], e)

    summary = {
        "wallets_considered": len(wallets),
        "snapshots_computed": computed,
        "empty_sample": empty,
        "errors": errors,
        "storage": wallet_storage_mode(),
        "note": "Snapshots reflect observed SWAP events only; incomplete history is disclosed in limitations.",
    }
    log.info(
        "wallet performance cycle wallets=%s computed=%s empty=%s errors=%s",
        summary["wallets_considered"],
        computed,
        empty,
        errors,
    )
    return summary
