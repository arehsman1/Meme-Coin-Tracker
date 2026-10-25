"""Wallet Intelligence service — public tracking independent of personal wallet connection."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from meme_tracker.models.enums import Chain, WalletEventType
from meme_tracker.wallets.classification import (
    DEFAULT_SIGNIFICANT_SWAP_USD,
    apply_significance,
)
from meme_tracker.wallets.models import (
    TrackedWallet,
    TrackedWalletCreate,
    WalletProfile,
    WalletTransaction,
    WalletTransactionIn,
)

logger = logging.getLogger(__name__)


class InMemoryWalletStore:
    """Test / offline store. Production path uses Supabase repository."""

    def __init__(self) -> None:
        self.wallets: dict[tuple[str, str], TrackedWallet] = {}
        self.transactions: list[WalletTransaction] = []
        self.profiles: dict[tuple[str, str], WalletProfile] = {}
        self._tx_keys: set[tuple[str, str, int, str]] = set()

    def upsert_tracked(self, data: TrackedWalletCreate) -> TrackedWallet:
        key = (data.chain.value, data.address)
        now = datetime.now(timezone.utc)
        existing = self.wallets.get(key)
        if existing:
            updated = existing.model_copy(
                update={
                    "label": data.label or existing.label,
                    "tags": data.tags or existing.tags,
                    "notes": data.notes if data.notes is not None else existing.notes,
                    "significant_swap_usd_threshold": (
                        data.significant_swap_usd_threshold
                        if data.significant_swap_usd_threshold is not None
                        else existing.significant_swap_usd_threshold
                    ),
                    "metadata": {**existing.metadata, **data.metadata},
                    "updated_at": now,
                    "is_active": True,
                }
            )
            self.wallets[key] = updated
            return updated
        wallet = TrackedWallet(
            id=uuid4(),
            **data.model_dump(),
            first_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        self.wallets[key] = wallet
        return wallet

    def list_tracked(self, active_only: bool = True) -> list[TrackedWallet]:
        items = list(self.wallets.values())
        if active_only:
            items = [w for w in items if w.is_active]
        return sorted(items, key=lambda w: (w.chain.value, w.address))

    def get_tracked(self, chain: Chain, address: str) -> TrackedWallet | None:
        return self.wallets.get((chain.value, address))

    def deactivate(self, chain: Chain, address: str) -> bool:
        key = (chain.value, address)
        w = self.wallets.get(key)
        if not w:
            return False
        self.wallets[key] = w.model_copy(
            update={"is_active": False, "updated_at": datetime.now(timezone.utc)}
        )
        return True

    def ingest_transaction(
        self,
        tx: WalletTransactionIn,
        threshold_usd: float | None = None,
    ) -> WalletTransaction | None:
        """Idempotent ingest. Returns None if duplicate."""
        log_idx = tx.log_index if tx.log_index is not None else -1
        key = (tx.chain.value, tx.tx_signature, log_idx, tx.wallet_address)
        if key in self._tx_keys:
            return None

        thr = threshold_usd
        if thr is None:
            tracked = self.get_tracked(tx.chain, tx.wallet_address)
            if tracked and tracked.significant_swap_usd_threshold is not None:
                thr = float(tracked.significant_swap_usd_threshold)
            else:
                thr = DEFAULT_SIGNIFICANT_SWAP_USD

        enriched = apply_significance(tx, threshold_usd=thr)
        record = WalletTransaction(
            id=uuid4(),
            **enriched.model_dump(),
            created_at=datetime.now(timezone.utc),
        )
        self.transactions.append(record)
        self._tx_keys.add(key)

        # Touch last_activity
        tracked = self.get_tracked(tx.chain, tx.wallet_address)
        if tracked:
            self.wallets[(tx.chain.value, tx.wallet_address)] = tracked.model_copy(
                update={"last_activity_at": record.block_time or record.created_at}
            )

        self._refresh_profile(tx.chain, tx.wallet_address)
        return record

    def list_transactions(
        self,
        chain: Chain | None = None,
        wallet_address: str | None = None,
        significant_only: bool = False,
        limit: int = 100,
    ) -> list[WalletTransaction]:
        items = self.transactions
        if chain:
            items = [t for t in items if t.chain == chain]
        if wallet_address:
            items = [t for t in items if t.wallet_address == wallet_address]
        if significant_only:
            items = [t for t in items if t.is_significant]
        items = sorted(
            items,
            key=lambda t: t.block_time or t.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return items[:limit]

    def _refresh_profile(self, chain: Chain, address: str) -> WalletProfile:
        txs = [t for t in self.transactions if t.chain == chain and t.wallet_address == address]
        swaps = [
            t
            for t in txs
            if t.event_type in (WalletEventType.SWAP_BUY, WalletEventType.SWAP_SELL)
        ]
        significant = [t for t in txs if t.is_significant]
        times = [t.block_time or t.created_at for t in txs if t.block_time or t.created_at]
        first = min(times) if times else None
        last = max(times) if times else None

        # Completeness: crude score based on known USD values
        known = sum(1 for t in swaps if t.value_usd_status.value == "KNOWN")
        completeness = int(100 * known / len(swaps)) if swaps else 0
        confidence = min(100, completeness + min(30, len(swaps) * 2))

        profile = WalletProfile(
            chain=chain,
            address=address,
            observed_trade_count=len(swaps),
            observed_significant_count=len(significant),
            first_observed_at=first,
            last_observed_at=last,
            data_completeness_score=completeness,
            confidence_score=confidence,
            updated_at=datetime.now(timezone.utc),
        )
        self.profiles[(chain.value, address)] = profile
        return profile

    def get_profile(self, chain: Chain, address: str) -> WalletProfile | None:
        return self.profiles.get((chain.value, address))


# Module-level store for memory mode (tests / no Supabase)
_store: InMemoryWalletStore | None = None


def get_wallet_store() -> InMemoryWalletStore:
    global _store
    if _store is None:
        _store = InMemoryWalletStore()
    return _store


def reset_wallet_store() -> None:
    global _store
    _store = InMemoryWalletStore()
