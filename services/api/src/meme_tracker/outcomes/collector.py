"""
Historical outcome collector.

After an evaluation, schedule horizon checks (5m, 15m, 1h, 6h, 24h, 7d).
Records price / liquidity deltas when data is available.
Does not feed look-ahead data back into the qualification engine.

Price multiples (2x/5x/…) describe token price vs a reference snapshot only.
They are never reported as wallet profit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from uuid import uuid4

from meme_tracker.models.enums import Chain
from meme_tracker.providers.registry import get_registry

log = logging.getLogger(__name__)

HORIZONS_MINUTES = [5, 15, 60, 360, 1440, 10080]  # 5m … 7d
PRICE_MULTIPLES = (2.0, 5.0, 10.0, 20.0)

# In-memory outcome rows when Supabase not available
_outcomes: list[dict[str, Any]] = []
# Reference prices keyed by evaluation_id
_references: dict[str, dict[str, Any]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reset_outcomes() -> None:
    """Test helper — clear in-memory store."""
    _outcomes.clear()
    _references.clear()


def set_reference_price(
    evaluation_id: str,
    *,
    price_usd: float | None,
    liquidity_usd: float | None = None,
    source: str = "evaluation",
    observed_at: datetime | None = None,
) -> None:
    """Store the decision-time reference used for later multiples (no look-ahead)."""
    _references[evaluation_id] = {
        "price_usd": price_usd,
        "liquidity_usd": liquidity_usd,
        "source": source,
        "observed_at": (observed_at or _utcnow()).isoformat(),
    }


def schedule_outcome_jobs(
    evaluation_id: str,
    chain: str,
    address: str,
    *,
    reference_price_usd: float | None = None,
    reference_liquidity_usd: float | None = None,
) -> list[str]:
    """
    Create delayed horizon rows (worker polls when due).
    Idempotent per (evaluation_id, horizon_minutes).
    """
    if reference_price_usd is not None or reference_liquidity_usd is not None:
        set_reference_price(
            evaluation_id,
            price_usd=reference_price_usd,
            liquidity_usd=reference_liquidity_usd,
            source="schedule",
        )

    existing = {
        (o.get("evaluation_id"), o.get("horizon_minutes"))
        for o in _outcomes
        if o.get("evaluation_id") == evaluation_id
    }

    ids: list[str] = []
    now = _utcnow()
    for h in HORIZONS_MINUTES:
        key = (evaluation_id, h)
        if key in existing:
            # Return existing id if present
            for o in _outcomes:
                if o.get("evaluation_id") == evaluation_id and o.get("horizon_minutes") == h:
                    ids.append(o["id"])
                    break
            continue

        oid = str(uuid4())
        row = {
            "id": oid,
            "evaluation_id": evaluation_id,
            "chain": chain,
            "address": address,
            "horizon_minutes": h,
            "due_at": (now + timedelta(minutes=h)).isoformat(),
            "outcome_status": "immature",
            "collected_at": None,
            "price_change_pct": None,
            "liquidity_change_pct": None,
            "liquidity_usd_snapshot": None,
            "price_usd_snapshot": None,
            "route_still_available": None,
            "reference_price_usd": (_references.get(evaluation_id) or {}).get("price_usd"),
            "multiples_reached": {},
            "limitations": [],
        }
        _outcomes.append(row)
        ids.append(oid)
    return ids


def due_outcomes(limit: int = 20) -> list[dict[str, Any]]:
    now = _utcnow()
    due: list[dict[str, Any]] = []
    for o in _outcomes:
        if o.get("outcome_status") != "immature":
            continue
        try:
            due_at = datetime.fromisoformat(str(o["due_at"]).replace("Z", "+00:00"))
        except Exception:
            continue
        if due_at <= now:
            due.append(o)
        if len(due) >= limit:
            break
    return due


def compute_multiples(
    reference_price: float | None,
    current_price: float | None,
) -> dict[str, bool]:
    """Token price multiples vs reference — not wallet profit."""
    out = {f"reached_{int(m)}x": False for m in PRICE_MULTIPLES}
    if reference_price is None or current_price is None:
        return out
    if reference_price <= 0 or current_price <= 0:
        return out
    ratio = current_price / reference_price
    for m in PRICE_MULTIPLES:
        out[f"reached_{int(m)}x"] = ratio >= m
    return out


def apply_market_snapshot(
    outcome: dict[str, Any],
    *,
    price_usd: float | None,
    liquidity_usd: float | None,
    price_change_pct: float | None = None,
) -> dict[str, Any]:
    """
    Pure function path for tests and providers.
    Fills outcome fields without calling external APIs.
    """
    limitations: list[str] = list(outcome.get("limitations") or [])
    ref = outcome.get("reference_price_usd")
    if ref is None:
        ref = (_references.get(str(outcome.get("evaluation_id"))) or {}).get("price_usd")
        outcome["reference_price_usd"] = ref

    if price_change_pct is None and ref is not None and price_usd is not None and ref > 0:
        price_change_pct = ((price_usd - ref) / ref) * 100.0

    liq_change = None
    ref_liq = (_references.get(str(outcome.get("evaluation_id"))) or {}).get("liquidity_usd")
    if ref_liq is not None and liquidity_usd is not None and ref_liq > 0:
        liq_change = ((liquidity_usd - ref_liq) / ref_liq) * 100.0

    multiples = compute_multiples(ref, price_usd)
    if ref is None:
        limitations.append("no_reference_price")
    if price_usd is None and liquidity_usd is None:
        limitations.append("no_market_snapshot")

    outcome["price_usd_snapshot"] = price_usd
    outcome["liquidity_usd_snapshot"] = liquidity_usd
    outcome["price_change_pct"] = price_change_pct
    outcome["liquidity_change_pct"] = liq_change
    outcome["route_still_available"] = (
        liquidity_usd is not None and liquidity_usd > 0
    )
    outcome["multiples_reached"] = multiples
    outcome["limitations"] = limitations
    outcome["collected_at"] = _utcnow().isoformat()

    if price_usd is not None or liquidity_usd is not None or price_change_pct is not None:
        outcome["outcome_status"] = "complete"
    else:
        outcome["outcome_status"] = "unavailable"

    return outcome


async def collect_one(outcome: dict[str, Any]) -> dict[str, Any]:
    """Attempt to fill an outcome row from DexScreener; mark unavailable on failure."""
    chain_s = outcome.get("chain")
    address = outcome.get("address")
    try:
        chain = Chain(chain_s)
    except Exception:
        outcome["outcome_status"] = "unavailable"
        outcome["collected_at"] = _utcnow().isoformat()
        outcome.setdefault("limitations", []).append("invalid_chain")
        return outcome

    registry = get_registry()
    dex = registry.get_dexscreener()
    if not dex:
        outcome["outcome_status"] = "unavailable"
        outcome["collected_at"] = _utcnow().isoformat()
        outcome.setdefault("limitations", []).append("dexscreener_unavailable")
        return outcome

    resp = await dex.get_token_pairs(chain, address)
    if not resp.ok or not resp.data:
        outcome["outcome_status"] = "unavailable"
        outcome["collected_at"] = _utcnow().isoformat()
        outcome.setdefault("limitations", []).append("provider_no_data")
        return outcome

    pairs = resp.data if isinstance(resp.data, list) else [resp.data]
    best_liq = None
    price_usd = None
    price_change = None
    for p in pairs:
        if not isinstance(p, dict):
            continue
        liq = p.get("liquidity") or {}
        usd = liq.get("usd") if isinstance(liq, dict) else None
        try:
            usd_f = float(usd) if usd is not None else None
        except (TypeError, ValueError):
            usd_f = None
        if usd_f is not None and (best_liq is None or usd_f > best_liq):
            best_liq = usd_f
        try:
            px = p.get("priceUsd")
            if px is not None:
                price_usd = float(px)
        except (TypeError, ValueError):
            pass
        pc = p.get("priceChange") or {}
        if isinstance(pc, dict):
            for key in ("m5", "h1", "h6", "h24"):
                if key in pc and pc[key] is not None:
                    try:
                        price_change = float(pc[key])
                        break
                    except (TypeError, ValueError):
                        pass

    return apply_market_snapshot(
        outcome,
        price_usd=price_usd,
        liquidity_usd=best_liq,
        price_change_pct=price_change,
    )


async def collect_due(limit: int = 10) -> int:
    due = due_outcomes(limit=limit)
    for o in due:
        await collect_one(o)
    return len(due)


def list_outcomes(evaluation_id: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    items = list(_outcomes)
    if evaluation_id:
        items = [o for o in items if o.get("evaluation_id") == evaluation_id]
    return items[-limit:]


def summarize_outcomes(evaluation_id: str | None = None) -> dict[str, Any]:
    """
    Aggregate metrics for reporting. Discloses sample size and incompleteness.
    Does not claim strategy profitability.
    """
    items = list_outcomes(evaluation_id=evaluation_id, limit=10_000)
    complete = [o for o in items if o.get("outcome_status") == "complete"]
    unavailable = [o for o in items if o.get("outcome_status") == "unavailable"]
    immature = [o for o in items if o.get("outcome_status") == "immature"]

    multiples_hits = {f"reached_{int(m)}x": 0 for m in PRICE_MULTIPLES}
    for o in complete:
        mr = o.get("multiples_reached") or {}
        for k in multiples_hits:
            if mr.get(k):
                multiples_hits[k] += 1

    with_change = [
        o["price_change_pct"]
        for o in complete
        if o.get("price_change_pct") is not None
    ]

    return {
        "sample_size": len(items),
        "complete": len(complete),
        "unavailable": len(unavailable),
        "immature": len(immature),
        "multiples_hits": multiples_hits,
        "avg_price_change_pct": (
            sum(with_change) / len(with_change) if with_change else None
        ),
        "limitations": [
            "Token price multiples are not wallet profits",
            "Incomplete provider data reduces complete count",
            "No look-ahead into qualification decisions",
        ],
        "disclaimer": (
            "Historical outcomes describe observed token market snapshots only. "
            "They do not establish future profitability."
        ),
    }
