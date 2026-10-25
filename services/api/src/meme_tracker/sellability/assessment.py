"""
Sellability assessment from market evidence (not a live swap simulation).

Sources:
  - DexScreener pair stats: buys/sells windows, liquidity USD, volume

Statuses (honest):
  VERIFIED  — multiple observed sell counts + meaningful liquidity
  LIMITED   — thin liquidity and/or sparse sells
  FAILED    — zero liquidity with market presence, or zero sells despite volume
  UNKNOWN   — insufficient pair data

This is NOT a guarantee of future sell success or non-honeypot status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from meme_tracker.models.enums import CheckResult, SellabilityStatus
from meme_tracker.models.evidence import SellabilityEvidence

# Thresholds (conservative)
MIN_LIQ_VERIFIED_USD = 5_000.0
MIN_LIQ_LIMITED_USD = 500.0
MIN_SELLS_VERIFIED = 5  # across best window we can see
MIN_SELLS_LIMITED = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tx_counts(pair: dict) -> dict[str, int]:
    """Extract buy/sell counts from DexScreener pair txns object."""
    txns = pair.get("txns") or {}
    out = {"buys": 0, "sells": 0, "window": "none"}
    if not isinstance(txns, dict):
        return out
    # Prefer longer windows when available
    for window in ("h24", "h6", "h1", "m5"):
        w = txns.get(window)
        if isinstance(w, dict):
            buys = int(w.get("buys") or 0)
            sells = int(w.get("sells") or 0)
            if buys or sells:
                return {"buys": buys, "sells": sells, "window": window}
    # Fallback first available
    for window, w in txns.items():
        if isinstance(w, dict):
            return {
                "buys": int(w.get("buys") or 0),
                "sells": int(w.get("sells") or 0),
                "window": str(window),
            }
    return out


def _liquidity_usd(pair: dict) -> Optional[float]:
    liq = pair.get("liquidity")
    if isinstance(liq, dict) and liq.get("usd") is not None:
        try:
            return float(liq["usd"])
        except (TypeError, ValueError):
            return None
    return None


def assess_from_dex_pairs(
    pairs: Any,
    *,
    source: str = "dexscreener",
) -> tuple[SellabilityEvidence, CheckResult, str]:
    """
    Assess sellability from DexScreener-style pair list.

    Returns (evidence, check_result for sellability_not_failed, human reason).
    """
    limitations = [
        "Based on market stats, not a live swap simulation",
        "Does not prove absence of transfer taxes or honeypot logic",
    ]

    if not pairs:
        ev = SellabilityEvidence(
            status=SellabilityStatus.UNKNOWN,
            method="none",
            limitations=limitations + ["no pair data"],
        )
        return ev, CheckResult.UNKNOWN, "No pair data for sellability"

    if not isinstance(pairs, list):
        pairs = [pairs]

    best_liq = 0.0
    total_sells = 0
    total_buys = 0
    windows: list[str] = []
    pair_count = 0

    for p in pairs:
        if not isinstance(p, dict):
            continue
        pair_count += 1
        liq = _liquidity_usd(p)
        if liq is not None and liq > best_liq:
            best_liq = liq
        counts = _tx_counts(p)
        total_sells += counts["sells"]
        total_buys += counts["buys"]
        if counts["window"] != "none":
            windows.append(counts["window"])

    if pair_count == 0:
        ev = SellabilityEvidence(
            status=SellabilityStatus.UNKNOWN,
            method="none",
            limitations=limitations + ["pairs malformed"],
        )
        return ev, CheckResult.UNKNOWN, "Pair payload unusable"

    refs = ["dex_pairs"]
    method = f"dex_pair_stats:{','.join(sorted(set(windows)) or ['n/a'])}"

    # FAILED: listed pairs but essentially no liquidity and no sells
    if best_liq < 1.0 and total_sells == 0:
        ev = SellabilityEvidence(
            status=SellabilityStatus.FAILED,
            method=method,
            route_available=False,
            limitations=limitations
            + [f"best_liquidity_usd={best_liq}", f"sells={total_sells}"],
            evidence_refs=refs,
        )
        return (
            ev,
            CheckResult.FAIL,
            f"No meaningful liquidity (${best_liq:.0f}) and zero observed sells",
        )

    # FAILED: volume-like activity (buys) but zero sells → honeypot-ish market signal
    if total_buys >= 10 and total_sells == 0 and best_liq < MIN_LIQ_VERIFIED_USD:
        ev = SellabilityEvidence(
            status=SellabilityStatus.FAILED,
            method=method,
            route_available=False,
            limitations=limitations
            + [
                f"buys={total_buys} sells=0",
                "Buy-heavy / zero-sell pattern is a risk signal, not proof of honeypot",
            ],
            evidence_refs=refs,
        )
        return (
            ev,
            CheckResult.FAIL,
            f"Buy activity ({total_buys}) with zero sells and modest liquidity",
        )

    # VERIFIED (still not a guarantee)
    if total_sells >= MIN_SELLS_VERIFIED and best_liq >= MIN_LIQ_VERIFIED_USD:
        ev = SellabilityEvidence(
            status=SellabilityStatus.VERIFIED,
            method=method,
            route_available=True,
            limitations=limitations
            + [
                f"observed_sells={total_sells}",
                f"best_liquidity_usd={best_liq:.0f}",
            ],
            evidence_refs=refs,
        )
        return (
            ev,
            CheckResult.PASS,
            f"Observed sells={total_sells}, liquidity≈${best_liq:,.0f} (market evidence only)",
        )

    # LIMITED
    if total_sells >= MIN_SELLS_LIMITED or best_liq >= MIN_LIQ_LIMITED_USD:
        ev = SellabilityEvidence(
            status=SellabilityStatus.LIMITED,
            method=method,
            route_available=best_liq >= MIN_LIQ_LIMITED_USD,
            limitations=limitations
            + [
                f"observed_sells={total_sells}",
                f"best_liquidity_usd={best_liq:.0f}",
                "Thin or sparse market — treat as limited exit capacity",
            ],
            evidence_refs=refs,
        )
        # LIMITED is not FAIL — check PASS with note
        return (
            ev,
            CheckResult.PASS,
            f"Limited sellability: sells={total_sells}, liq≈${best_liq:,.0f}",
        )

    # Still thin data
    ev = SellabilityEvidence(
        status=SellabilityStatus.UNKNOWN,
        method=method,
        route_available=None,
        limitations=limitations
        + [f"sells={total_sells}", f"best_liquidity_usd={best_liq:.0f}"],
        evidence_refs=refs,
    )
    return (
        ev,
        CheckResult.UNKNOWN,
        f"Insufficient sell evidence (sells={total_sells}, liq≈${best_liq:,.0f})",
    )
