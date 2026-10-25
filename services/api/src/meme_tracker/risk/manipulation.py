"""Manipulation and concentration risk analysis.

Produces structured findings and CheckOutcome rows.
Indicators are evidence of *possible* risk, not proof of intent.
Missing data → UNKNOWN checks, never silent PASS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from meme_tracker.models.enums import CheckResult
from meme_tracker.models.evidence import (
    CheckOutcome,
    HolderSnapshot,
    LiquiditySnapshot,
)

# Provisional thresholds (policy-labelled; match IMPLEMENTATION_PLAN)
TOP_HOLDER_HIGH = 40.0
TOP_HOLDER_ELEVATED = 25.0
TOP10_HIGH = 70.0
TOP10_ELEVATED = 50.0
CREATOR_HIGH = 15.0
CREATOR_ELEVATED = 8.0
MIN_HOLDERS_THIN = 30
LIQ_VERY_THIN_USD = 2_000.0
LIQ_THIN_USD = 10_000.0
BUY_SELL_RATIO_SKEW = 8.0


@dataclass
class MarketActivitySignals:
    """Optional activity inputs when providers supply them."""

    buy_volume_usd: float | None = None
    sell_volume_usd: float | None = None
    tx_count_window: int | None = None
    unique_buyers_window: int | None = None
    unique_sellers_window: int | None = None
    window_seconds: int | None = None
    pool_age_seconds: float | None = None
    source: str = "unknown"


@dataclass
class ManipulationFinding:
    signal_type: str
    severity: str  # low | medium | high | critical
    confidence: str  # CONFIRMED | PROBABLE | POSSIBLE | UNKNOWN
    detail: str
    contribution: float


@dataclass
class ManipulationAssessment:
    findings: list[ManipulationFinding] = field(default_factory=list)
    checks: list[CheckOutcome] = field(default_factory=list)
    manipulation_risk_score: float = 0.0
    data_gaps: list[str] = field(default_factory=list)


def analyze_manipulation(
    *,
    holders: Optional[HolderSnapshot] = None,
    liquidity: Optional[list[LiquiditySnapshot]] = None,
    activity: Optional[MarketActivitySignals] = None,
) -> ManipulationAssessment:
    """
    Deterministic manipulation / concentration assessment.
    Does not invent holder or volume data.
    """
    findings: list[ManipulationFinding] = []
    checks: list[CheckOutcome] = []
    gaps: list[str] = []
    risk = 12.0  # base residual uncertainty

    if holders is None:
        gaps.append("holders")
        checks.append(
            CheckOutcome(
                check_id="holder_concentration_known",
                name="Holder concentration data present",
                result=CheckResult.UNKNOWN,
                severity="high",
                reason="Holder snapshot unavailable",
                evidence_refs=[],
                is_required=False,
            )
        )
    else:
        top = holders.top_holder_pct
        top10 = holders.top10_pct
        creator = holders.creator_pct
        n_holders = holders.unique_holders

        if top is None and top10 is None and creator is None:
            gaps.append("holder_pcts")
            checks.append(
                CheckOutcome(
                    check_id="holder_concentration_known",
                    name="Holder concentration data present",
                    result=CheckResult.UNKNOWN,
                    severity="high",
                    reason="Holder percentages not measurable from source",
                    evidence_refs=[holders.source],
                    is_required=False,
                )
            )
        else:
            checks.append(
                CheckOutcome(
                    check_id="holder_concentration_known",
                    name="Holder concentration data present",
                    result=CheckResult.PASS,
                    severity="low",
                    reason="Holder concentration data present",
                    evidence_refs=[holders.source],
                    is_required=False,
                )
            )

        if top is not None:
            if top > TOP_HOLDER_HIGH:
                findings.append(
                    ManipulationFinding(
                        signal_type="top_holder_concentration",
                        severity="high",
                        confidence="PROBABLE",
                        detail=f"Top holder controls {top:.1f}% of observed supply sample",
                        contribution=28.0,
                    )
                )
                checks.append(
                    CheckOutcome(
                        check_id="top_holder_not_excessive",
                        name="Top holder share within limit",
                        result=CheckResult.FAIL,
                        severity="high",
                        reason=f"Top holder {top:.1f}% exceeds {TOP_HOLDER_HIGH}%",
                        evidence_refs=[holders.source],
                        is_required=False,
                    )
                )
            elif top > TOP_HOLDER_ELEVATED:
                findings.append(
                    ManipulationFinding(
                        signal_type="top_holder_concentration",
                        severity="medium",
                        confidence="POSSIBLE",
                        detail=f"Top holder controls {top:.1f}% (elevated)",
                        contribution=14.0,
                    )
                )
                checks.append(
                    CheckOutcome(
                        check_id="top_holder_not_excessive",
                        name="Top holder share within limit",
                        result=CheckResult.PASS,
                        severity="normal",
                        reason=f"Top holder {top:.1f}% elevated but under hard threshold",
                        evidence_refs=[holders.source],
                        is_required=False,
                    )
                )
            else:
                checks.append(
                    CheckOutcome(
                        check_id="top_holder_not_excessive",
                        name="Top holder share within limit",
                        result=CheckResult.PASS,
                        severity="low",
                        reason=f"Top holder {top:.1f}% within provisional band",
                        evidence_refs=[holders.source],
                        is_required=False,
                    )
                )

        if top10 is not None:
            if top10 > TOP10_HIGH:
                findings.append(
                    ManipulationFinding(
                        signal_type="top10_concentration",
                        severity="high",
                        confidence="PROBABLE",
                        detail=f"Top-10 holders control {top10:.1f}%",
                        contribution=18.0,
                    )
                )
            elif top10 > TOP10_ELEVATED:
                findings.append(
                    ManipulationFinding(
                        signal_type="top10_concentration",
                        severity="medium",
                        confidence="POSSIBLE",
                        detail=f"Top-10 holders control {top10:.1f}% (elevated)",
                        contribution=9.0,
                    )
                )

        if creator is not None:
            if creator > CREATOR_HIGH:
                findings.append(
                    ManipulationFinding(
                        signal_type="creator_concentration",
                        severity="high",
                        confidence="PROBABLE",
                        detail=f"Creator/deployer-linked share ~{creator:.1f}%",
                        contribution=16.0,
                    )
                )
                checks.append(
                    CheckOutcome(
                        check_id="creator_share_not_excessive",
                        name="Creator share within limit",
                        result=CheckResult.FAIL,
                        severity="high",
                        reason=f"Creator share {creator:.1f}% exceeds {CREATOR_HIGH}%",
                        evidence_refs=[holders.source],
                        is_required=False,
                    )
                )
            elif creator > CREATOR_ELEVATED:
                findings.append(
                    ManipulationFinding(
                        signal_type="creator_concentration",
                        severity="medium",
                        confidence="POSSIBLE",
                        detail=f"Creator share ~{creator:.1f}% elevated",
                        contribution=8.0,
                    )
                )

        if n_holders is not None and n_holders < MIN_HOLDERS_THIN:
            findings.append(
                ManipulationFinding(
                    signal_type="thin_holder_base",
                    severity="medium",
                    confidence="POSSIBLE",
                    detail=f"Only {n_holders} unique holders observed",
                    contribution=10.0,
                )
            )

    liq_list = liquidity or []
    max_liq = max((ls.liquidity_usd or 0.0) for ls in liq_list) if liq_list else None
    if max_liq is None:
        gaps.append("liquidity")
    elif max_liq < LIQ_VERY_THIN_USD:
        findings.append(
            ManipulationFinding(
                signal_type="very_thin_liquidity",
                severity="high",
                confidence="PROBABLE",
                detail=f"Max observed liquidity ~${max_liq:,.0f}",
                contribution=15.0,
            )
        )
    elif max_liq < LIQ_THIN_USD:
        findings.append(
            ManipulationFinding(
                signal_type="thin_liquidity",
                severity="medium",
                confidence="POSSIBLE",
                detail=f"Max observed liquidity ~${max_liq:,.0f}",
                contribution=7.0,
            )
        )

    if activity is None:
        gaps.append("market_activity")
    else:
        bv, sv = activity.buy_volume_usd, activity.sell_volume_usd
        if bv is not None and sv is not None:
            if sv <= 0 and bv > 0:
                findings.append(
                    ManipulationFinding(
                        signal_type="buy_heavy_zero_sells",
                        severity="high",
                        confidence="POSSIBLE",
                        detail=f"Buy volume ${bv:,.0f} with ~0 sells in window",
                        contribution=20.0,
                    )
                )
            elif sv > 0 and bv / sv >= BUY_SELL_RATIO_SKEW:
                findings.append(
                    ManipulationFinding(
                        signal_type="buy_sell_volume_skew",
                        severity="medium",
                        confidence="POSSIBLE",
                        detail=f"Buy/sell volume ratio ≈ {bv / sv:.1f}x in window",
                        contribution=12.0,
                    )
                )
        else:
            gaps.append("buy_sell_volumes")

        if (
            activity.unique_buyers_window is not None
            and activity.tx_count_window is not None
            and activity.tx_count_window >= 10
            and activity.unique_buyers_window <= 3
        ):
            findings.append(
                ManipulationFinding(
                    signal_type="concentrated_buyers",
                    severity="medium",
                    confidence="POSSIBLE",
                    detail=(
                        f"{activity.unique_buyers_window} unique buyers across "
                        f"{activity.tx_count_window} txs in window"
                    ),
                    contribution=14.0,
                )
            )

        if activity.pool_age_seconds is not None and activity.pool_age_seconds < 600:
            findings.append(
                ManipulationFinding(
                    signal_type="very_new_pool",
                    severity="low",
                    confidence="CONFIRMED",
                    detail=f"Pool age ~{activity.pool_age_seconds:.0f}s",
                    contribution=5.0,
                )
            )

    for f in findings:
        risk += f.contribution
    risk = min(100.0, max(0.0, risk))

    return ManipulationAssessment(
        findings=findings,
        checks=checks,
        manipulation_risk_score=round(risk, 1),
        data_gaps=gaps,
    )


def findings_as_dicts(assessment: ManipulationAssessment) -> list[dict[str, Any]]:
    return [
        {
            "signal_type": f.signal_type,
            "severity": f.severity,
            "confidence": f.confidence,
            "detail": f.detail,
            "contribution": f.contribution,
        }
        for f in assessment.findings
    ]
