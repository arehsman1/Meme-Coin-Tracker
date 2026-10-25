"""Wallet relationship inference — evidence-based, never claims ownership from co-buying alone."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from meme_tracker.models.enums import RelationshipConfidence
from meme_tracker.wallets.models import WalletTransaction


@dataclass
class RelationshipEdge:
    chain: str
    wallet_a: str
    wallet_b: str
    relationship_type: str
    confidence: RelationshipConfidence
    evidence: list[dict]
    notes: str


def _norm_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def detect_coordinated_token_buys(
    chain: str,
    transactions: Iterable[WalletTransaction],
    *,
    min_wallets: int = 2,
    window_seconds: int = 300,
) -> list[RelationshipEdge]:
    """
    Flag POSSIBLE coordination when multiple wallets buy the same token
    within a time window. Never elevates to CONFIRMED without on-chain funding links.
    """
    # token -> list of (wallet, ts)
    by_token: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for tx in transactions:
        et = tx.event_type.value if hasattr(tx.event_type, "value") else str(tx.event_type)
        if et != "SWAP_BUY" or not tx.token_address:
            continue
        ts = tx.block_time or tx.created_at
        if not ts:
            continue
        by_token[tx.token_address].append((tx.wallet_address, ts.timestamp()))

    edges: list[RelationshipEdge] = []
    seen_pairs: set[tuple[str, str, str]] = set()

    for token, events in by_token.items():
        events.sort(key=lambda x: x[1])
        for i, (w1, t1) in enumerate(events):
            cohort = [w1]
            for w2, t2 in events[i + 1 :]:
                if t2 - t1 > window_seconds:
                    break
                if w2 != w1:
                    cohort.append(w2)
            uniq = sorted(set(cohort))
            if len(uniq) < min_wallets:
                continue
            for i_a in range(len(uniq)):
                for i_b in range(i_a + 1, len(uniq)):
                    a, b = _norm_pair(uniq[i_a], uniq[i_b])
                    key = (a, b, token)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    edges.append(
                        RelationshipEdge(
                            chain=chain,
                            wallet_a=a,
                            wallet_b=b,
                            relationship_type="synchronized_buy",
                            confidence=RelationshipConfidence.POSSIBLE,
                            evidence=[
                                {
                                    "type": "same_token_buy_window",
                                    "token_address": token,
                                    "window_seconds": window_seconds,
                                }
                            ],
                            notes=(
                                "Statistical co-occurrence only. "
                                "Does not prove common ownership or funding relationship."
                            ),
                        )
                    )
    return edges


def edge_to_dict(edge: RelationshipEdge) -> dict:
    return {
        "chain": edge.chain,
        "wallet_a": edge.wallet_a,
        "wallet_b": edge.wallet_b,
        "relationship_type": edge.relationship_type,
        "confidence": edge.confidence.value,
        "evidence": edge.evidence,
        "notes": edge.notes,
    }
