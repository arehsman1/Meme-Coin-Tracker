"""Position ledger and performance metrics for observed wallet activity.

Rules (master spec):
- Only SWAP_BUY / SWAP_SELL affect cost basis and realized PnL.
- Transfers do not open or close trading positions.
- UNKNOWN USD amounts do not invent PnL.
- Partial exits reduce open qty; remaining cost basis is proportional (average cost).
- Never report token price peaks as wallet profit.
- Always expose sample_size, completeness, and limitations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from meme_tracker.models.enums import ValueUsdStatus, WalletEventType
from meme_tracker.wallets.models import WalletTransaction

SCORING_RULE_VERSION = "v0.1"


@dataclass
class PositionState:
    chain: str
    wallet_address: str
    token_address: str
    qty_open: float = 0.0
    cost_basis_usd: float | None = None
    cost_basis_status: str = "UNKNOWN"
    realized_pnl_usd: float = 0.0
    realized_pnl_status: str = "UNKNOWN"
    total_bought_qty: float = 0.0
    total_sold_qty: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    first_entry_at: datetime | None = None
    last_exit_at: datetime | None = None
    last_event_at: datetime | None = None
    closed_wins: int = 0
    closed_losses: int = 0
    closed_unknown: int = 0
    limitations: list[str] = field(default_factory=list)

    @property
    def closed_trade_count(self) -> int:
        return self.closed_wins + self.closed_losses + self.closed_unknown


@dataclass
class PerformanceSnapshot:
    chain: str
    wallet_address: str
    observed_trade_count: int
    closed_trade_count: int
    win_count: int
    loss_count: int
    win_rate: float | None
    realized_pnl_usd: float | None
    realized_pnl_status: str
    sample_size: int
    data_completeness_score: int
    confidence_score: int
    observation_period_start: datetime | None
    observation_period_end: datetime | None
    limitations: list[str]
    scoring_rule_version: str
    components: dict[str, float]
    positions: list[PositionState] = field(default_factory=list)


def _event_type(tx: WalletTransaction) -> str:
    et = tx.event_type
    return et.value if hasattr(et, "value") else str(et)


def _usd_status(tx: WalletTransaction) -> str:
    s = tx.value_usd_status
    return s.value if hasattr(s, "value") else str(s)


def apply_swap_to_position(pos: PositionState, tx: WalletTransaction) -> None:
    """Update one token position from a single swap. Mutates pos."""
    et = _event_type(tx)
    qty = float(tx.amount_ui) if tx.amount_ui is not None else None
    usd = tx.value_usd
    usd_st = _usd_status(tx)
    ts = tx.block_time or tx.created_at or datetime.now(timezone.utc)

    if et not in (WalletEventType.SWAP_BUY.value, WalletEventType.SWAP_SELL.value, "SWAP_BUY", "SWAP_SELL"):
        return

    if qty is None or qty <= 0:
        pos.limitations.append(f"missing_qty:{tx.tx_signature[:12]}")
        return

    pos.last_event_at = ts

    if et in (WalletEventType.SWAP_BUY.value, "SWAP_BUY"):
        pos.buy_count += 1
        pos.total_bought_qty += qty
        if pos.first_entry_at is None:
            pos.first_entry_at = ts

        if usd is not None and usd_st == "KNOWN":
            if pos.cost_basis_usd is None or pos.cost_basis_status == "UNKNOWN":
                pos.cost_basis_usd = float(usd)
                pos.cost_basis_status = "KNOWN"
            else:
                pos.cost_basis_usd = float(pos.cost_basis_usd) + float(usd)
                if pos.cost_basis_status != "KNOWN":
                    pos.cost_basis_status = "MIXED"
            pos.qty_open += qty
        else:
            pos.qty_open += qty
            if pos.cost_basis_status == "KNOWN":
                pos.cost_basis_status = "MIXED"
            else:
                pos.cost_basis_status = "UNKNOWN"
            pos.limitations.append(f"buy_unknown_usd:{tx.tx_signature[:12]}")

    elif et in (WalletEventType.SWAP_SELL.value, "SWAP_SELL"):
        pos.sell_count += 1
        pos.total_sold_qty += qty
        pos.last_exit_at = ts

        sell_qty = min(qty, pos.qty_open) if pos.qty_open > 0 else 0.0
        if sell_qty <= 0:
            # Sold without observed open position — incomplete history
            pos.limitations.append(f"sell_without_open:{tx.tx_signature[:12]}")
            pos.closed_unknown += 1
            if pos.realized_pnl_status == "KNOWN":
                pos.realized_pnl_status = "PARTIAL"
            else:
                pos.realized_pnl_status = "UNKNOWN"
            return

        # Average-cost exit
        if (
            pos.cost_basis_usd is not None
            and pos.qty_open > 0
            and pos.cost_basis_status in ("KNOWN", "MIXED", "ESTIMATED")
            and usd is not None
            and usd_st == "KNOWN"
        ):
            avg_cost = float(pos.cost_basis_usd) / pos.qty_open
            cost_exited = avg_cost * sell_qty
            # Scale sale USD if only partial of the reported sell matches open
            sale_usd = float(usd) * (sell_qty / qty) if qty > 0 else float(usd)
            pnl = sale_usd - cost_exited
            pos.realized_pnl_usd = float(pos.realized_pnl_usd) + pnl
            if pos.realized_pnl_status == "UNKNOWN":
                pos.realized_pnl_status = "KNOWN"
            pos.cost_basis_usd = float(pos.cost_basis_usd) - cost_exited
            pos.qty_open -= sell_qty
            if pos.qty_open <= 1e-18:
                pos.qty_open = 0.0
                pos.cost_basis_usd = 0.0
            if pnl > 0:
                pos.closed_wins += 1
            elif pnl < 0:
                pos.closed_losses += 1
            else:
                pos.closed_unknown += 1
        else:
            pos.qty_open = max(0.0, pos.qty_open - sell_qty)
            pos.closed_unknown += 1
            pos.realized_pnl_status = (
                "PARTIAL" if pos.realized_pnl_status == "KNOWN" else "UNKNOWN"
            )
            pos.limitations.append(f"sell_incomplete_basis:{tx.tx_signature[:12]}")


def build_positions_from_transactions(
    chain: str,
    wallet_address: str,
    transactions: list[WalletTransaction],
) -> dict[str, PositionState]:
    """Chronological ledger build. Keyed by token_address."""
    # Oldest first
    ordered = sorted(
        transactions,
        key=lambda t: t.block_time
        or t.created_at
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    positions: dict[str, PositionState] = {}
    for tx in ordered:
        et = _event_type(tx)
        if et not in ("SWAP_BUY", "SWAP_SELL"):
            continue
        token = tx.token_address or "_unknown_"
        if token not in positions:
            positions[token] = PositionState(
                chain=chain,
                wallet_address=wallet_address,
                token_address=token,
            )
        apply_swap_to_position(positions[token], tx)
    return positions


def compute_performance(
    chain: str,
    wallet_address: str,
    transactions: list[WalletTransaction],
) -> PerformanceSnapshot:
    positions = build_positions_from_transactions(chain, wallet_address, transactions)
    swaps = [
        t
        for t in transactions
        if _event_type(t) in ("SWAP_BUY", "SWAP_SELL")
    ]
    known_usd = sum(1 for t in swaps if _usd_status(t) == "KNOWN" and t.value_usd is not None)
    completeness = int(100 * known_usd / len(swaps)) if swaps else 0

    wins = sum(p.closed_wins for p in positions.values())
    losses = sum(p.closed_losses for p in positions.values())
    closed = wins + losses + sum(p.closed_unknown for p in positions.values())
    win_rate = (wins / (wins + losses)) if (wins + losses) > 0 else None

    realized_vals = [
        p.realized_pnl_usd
        for p in positions.values()
        if p.realized_pnl_status in ("KNOWN", "PARTIAL", "ESTIMATED")
    ]
    realized_sum = sum(realized_vals) if realized_vals else None
    if any(p.realized_pnl_status == "KNOWN" for p in positions.values()):
        if any(p.realized_pnl_status in ("PARTIAL", "UNKNOWN") for p in positions.values()):
            realized_status = "PARTIAL"
        else:
            realized_status = "KNOWN"
    else:
        realized_status = "UNKNOWN"

    limitations: list[str] = []
    for p in positions.values():
        limitations.extend(p.limitations[:5])
    if completeness < 100:
        limitations.append("incomplete_usd_coverage")
    if any(p.qty_open > 0 for p in positions.values()):
        limitations.append("open_positions_unrealized_not_marked")
    # Dedupe preserve order
    seen: set[str] = set()
    uniq_lim: list[str] = []
    for x in limitations:
        if x not in seen:
            seen.add(x)
            uniq_lim.append(x)

    times = [
        t.block_time or t.created_at
        for t in swaps
        if t.block_time or t.created_at
    ]
    period_start = min(times) if times else None
    period_end = max(times) if times else None

    # Confidence: completeness + sample size, capped
    confidence = min(100, completeness + min(30, len(swaps) * 2))
    if closed < 3:
        confidence = min(confidence, 40 + closed * 10)
        uniq_lim.append("small_sample")

    components = score_components(
        win_rate=win_rate,
        closed=closed,
        completeness=completeness,
        realized_pnl=realized_sum,
        trade_count=len(swaps),
    )

    return PerformanceSnapshot(
        chain=chain,
        wallet_address=wallet_address,
        observed_trade_count=len(swaps),
        closed_trade_count=closed,
        win_count=wins,
        loss_count=losses,
        win_rate=win_rate,
        realized_pnl_usd=realized_sum,
        realized_pnl_status=realized_status,
        sample_size=len(swaps),
        data_completeness_score=completeness,
        confidence_score=confidence,
        observation_period_start=period_start,
        observation_period_end=period_end,
        limitations=uniq_lim[:20],
        scoring_rule_version=SCORING_RULE_VERSION,
        components=components,
        positions=list(positions.values()),
    )


def score_components(
    *,
    win_rate: float | None,
    closed: int,
    completeness: int,
    realized_pnl: float | None,
    trade_count: int,
) -> dict[str, float]:
    """
    Versioned score components. Quality and confidence stay separate.
    High win rate on 2 trades must not look as reliable as a larger sample.
    """
    sample_factor = min(1.0, closed / 20.0) if closed else 0.0
    win_component = (win_rate if win_rate is not None else 0.5) * sample_factor * 40.0
    completeness_component = (completeness / 100.0) * 25.0
    activity_component = min(20.0, trade_count * 1.5)
    pnl_component = 0.0
    if realized_pnl is not None and realized_pnl != 0:
        # Bounded contribution — not a profitability claim
        pnl_component = max(-15.0, min(15.0, realized_pnl / 1000.0 * 5.0))
    return {
        "win_rate_weighted": round(win_component, 4),
        "completeness": round(completeness_component, 4),
        "activity": round(activity_component, 4),
        "realized_pnl_bounded": round(pnl_component, 4),
        "sample_factor": round(sample_factor, 4),
        "composite_quality": round(
            win_component + completeness_component + activity_component + pnl_component,
            4,
        ),
    }


def snapshot_to_dict(snap: PerformanceSnapshot) -> dict[str, Any]:
    return {
        "chain": snap.chain,
        "wallet_address": snap.wallet_address,
        "observed_trade_count": snap.observed_trade_count,
        "closed_trade_count": snap.closed_trade_count,
        "win_count": snap.win_count,
        "loss_count": snap.loss_count,
        "win_rate": snap.win_rate,
        "realized_pnl_usd": snap.realized_pnl_usd,
        "realized_pnl_status": snap.realized_pnl_status,
        "sample_size": snap.sample_size,
        "data_completeness_score": snap.data_completeness_score,
        "confidence_score": snap.confidence_score,
        "observation_period_start": (
            snap.observation_period_start.isoformat()
            if snap.observation_period_start
            else None
        ),
        "observation_period_end": (
            snap.observation_period_end.isoformat() if snap.observation_period_end else None
        ),
        "limitations": snap.limitations,
        "scoring_rule_version": snap.scoring_rule_version,
        "components": snap.components,
        "open_positions": [
            {
                "token_address": p.token_address,
                "qty_open": p.qty_open,
                "cost_basis_status": p.cost_basis_status,
                "realized_pnl_usd": p.realized_pnl_usd,
                "realized_pnl_status": p.realized_pnl_status,
                "buy_count": p.buy_count,
                "sell_count": p.sell_count,
            }
            for p in snap.positions
            if p.qty_open > 0 or p.buy_count or p.sell_count
        ],
        "disclaimer": (
            "Performance is derived only from observed SWAP events. "
            "Incomplete history, unknown USD values, and open positions limit reliability. "
            "Not a prediction of future results."
        ),
    }
