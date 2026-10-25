"""
Authoritative qualification engine.

Deterministic given the same inputs + policy_version.
Critical FAIL always overrides high scores.
UNKNOWN on critical required checks prevents qualification.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from meme_tracker import ENGINE_VERSION, POLICY_VERSION
from meme_tracker.models.enums import (
    Chain,
    CheckResult,
    QualificationStatus,
    SellabilityStatus,
)
from meme_tracker.models.evidence import (
    CheckOutcome,
    EvidenceItem,
    HolderSnapshot,
    LiquiditySnapshot,
    QualificationDecision,
    ScoreComponent,
    ScoreSet,
    SellabilityEvidence,
    TokenIdentity,
)


# ---------------------------------------------------------------------------
# Policy configuration (versioned)
# ---------------------------------------------------------------------------

CRITICAL_CHECK_IDS = {
    "identity_valid",
    "mint_authority_renounced_or_safe",
    "freeze_authority_safe",
    "no_blacklist_or_pause_trap",
    "sellability_not_failed",
    "liquidity_present",
}

# Minimum data confidence required before QUALIFIED_TRADE_CANDIDATE
MIN_CONFIDENCE_FOR_QUALIFY = 55.0
# Max security / manipulation risk allowed for qualification
MAX_SECURITY_RISK_FOR_QUALIFY = 40.0
MAX_MANIPULATION_RISK_FOR_QUALIFY = 55.0
# Minimum trade quality
MIN_TRADE_QUALITY_FOR_QUALIFY = 45.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QualificationEngine:
    """
    Single authoritative qualification engine.
    All chains feed evidence into this engine.
    """

    def __init__(
        self,
        policy_version: str = POLICY_VERSION,
        engine_version: str = ENGINE_VERSION,
    ) -> None:
        self.policy_version = policy_version
        self.engine_version = engine_version

    def evaluate(
        self,
        identity: TokenIdentity,
        evidence: list[EvidenceItem],
        checks: list[CheckOutcome],
        sellability: Optional[SellabilityEvidence] = None,
        liquidity: Optional[list[LiquiditySnapshot]] = None,
        holders: Optional[HolderSnapshot] = None,
        evaluation_id: Optional[str] = None,
        revision: int = 1,
    ) -> QualificationDecision:
        """
        Produce a deterministic QualificationDecision.
        """
        evaluation_id = evaluation_id or str(uuid.uuid4())
        liquidity = liquidity or []
        missing: list[str] = []
        conflicts: list[str] = []

        # Collect missing / conflicting from evidence
        for item in evidence:
            if item.retrieval_status in ("missing", "error", "timeout"):
                missing.append(f"{item.key}:{item.source}")
            if item.conflicting_values:
                conflicts.append(f"{item.key}:{item.source}")

        # Append structured manipulation / concentration checks (non-destructive)
        from meme_tracker.risk.manipulation import analyze_manipulation

        manip_pre = analyze_manipulation(
            holders=holders, liquidity=liquidity, activity=None
        )
        merged_checks = list(checks) + list(manip_pre.checks)
        for g in manip_pre.data_gaps:
            if g not in missing:
                missing.append(f"manip_gap:{g}")

        # Score calculation
        scores = self._calculate_scores(
            merged_checks, evidence, sellability, liquidity, holders
        )

        # Status determination (deterministic precedence)
        final_status, reasons = self._determine_status(
            checks=merged_checks,
            scores=scores,
            sellability=sellability,
            missing=missing,
            conflicts=conflicts,
        )

        freshness = self._freshness_summary(evidence)

        return QualificationDecision(
            evaluation_id=evaluation_id,
            revision=revision,
            chain=identity.chain,
            token_address=identity.address,
            policy_version=self.policy_version,
            engine_version=self.engine_version,
            identity=identity,
            checks=merged_checks,
            scores=scores,
            final_status=final_status,
            reasons=reasons,
            missing_evidence=missing,
            conflicting_evidence=conflicts,
            data_freshness_summary=freshness,
            sellability=sellability,
            liquidity=liquidity,
            holders=holders,
            evaluated_at=_utcnow(),
            evidence_items=evidence,
        )

    def _determine_status(
        self,
        checks: list[CheckOutcome],
        scores: ScoreSet,
        sellability: Optional[SellabilityEvidence],
        missing: list[str],
        conflicts: list[str],
    ) -> tuple[QualificationStatus, list[str]]:
        reasons: list[str] = []

        # 1. Critical FAIL → REJECTED
        critical_fails = [
            c for c in checks if c.result == CheckResult.FAIL and c.check_id in CRITICAL_CHECK_IDS
        ]
        if critical_fails:
            for c in critical_fails:
                reasons.append(f"CRITICAL FAIL: {c.name} — {c.reason}")
            return QualificationStatus.REJECTED_CRITICAL_RISK, reasons

        # 2. Sellability explicitly FAILED → REJECTED
        if sellability and sellability.status == SellabilityStatus.FAILED:
            reasons.append("Sellability check FAILED with supporting evidence")
            return QualificationStatus.REJECTED_CRITICAL_RISK, reasons

        # 3. Required critical checks UNKNOWN or missing → PENDING
        critical_unknown = [
            c
            for c in checks
            if c.is_required
            and c.check_id in CRITICAL_CHECK_IDS
            and c.result in (CheckResult.UNKNOWN, CheckResult.NOT_APPLICABLE)
        ]
        if critical_unknown or (missing and any("identity" in m or "mint" in m for m in missing)):
            for c in critical_unknown:
                reasons.append(f"Missing/unknown critical: {c.name}")
            if missing:
                reasons.append(f"Missing evidence items: {len(missing)}")
            return QualificationStatus.PENDING_DATA_INCOMPLETE, reasons

        # 4. Low data confidence → PENDING
        if scores.data_confidence_score < 40.0:
            reasons.append(
                f"Data confidence too low ({scores.data_confidence_score:.1f} < 40)"
            )
            return QualificationStatus.PENDING_DATA_INCOMPLETE, reasons

        # 5. High risk but not critical → MONITOR
        if (
            scores.security_risk_score > MAX_SECURITY_RISK_FOR_QUALIFY
            or scores.manipulation_risk_score > MAX_MANIPULATION_RISK_FOR_QUALIFY
        ):
            reasons.append(
                f"Elevated risk (security={scores.security_risk_score:.1f}, "
                f"manipulation={scores.manipulation_risk_score:.1f})"
            )
            return QualificationStatus.MONITOR_NOT_YET_QUALIFIED, reasons

        # 6. Security ok but trade quality insufficient
        if scores.trade_quality_score < MIN_TRADE_QUALITY_FOR_QUALIFY:
            reasons.append(
                f"Trade quality below threshold ({scores.trade_quality_score:.1f})"
            )
            return QualificationStatus.SECURITY_QUALIFIED_NO_TRADE_SIGNAL, reasons

        # 7. Confidence still marginal
        if scores.data_confidence_score < MIN_CONFIDENCE_FOR_QUALIFY:
            reasons.append(
                f"Confidence below qualify threshold ({scores.data_confidence_score:.1f})"
            )
            return QualificationStatus.MONITOR_NOT_YET_QUALIFIED, reasons

        # 8. All gates passed
        reasons.append("All mandatory safety gates passed and trade criteria met")
        return QualificationStatus.QUALIFIED_TRADE_CANDIDATE, reasons

    def _calculate_scores(
        self,
        checks: list[CheckOutcome],
        evidence: list[EvidenceItem],
        sellability: Optional[SellabilityEvidence],
        liquidity: list[LiquiditySnapshot],
        holders: Optional[HolderSnapshot],
    ) -> ScoreSet:
        components: list[ScoreComponent] = []

        # --- Security risk (higher = worse) ---
        security = 20.0  # base
        for c in checks:
            if c.result == CheckResult.FAIL:
                if c.severity == "critical":
                    security += 35.0
                elif c.severity == "high":
                    security += 20.0
                else:
                    security += 8.0
            elif c.result == CheckResult.UNKNOWN and c.is_required:
                security += 12.0
        if sellability and sellability.status == SellabilityStatus.FAILED:
            security += 30.0
        elif sellability and sellability.status == SellabilityStatus.UNKNOWN:
            security += 10.0
        security = min(100.0, max(0.0, security))
        components.append(
            ScoreComponent(
                name="security_base_and_fails",
                weight=1.0,
                raw_value=security,
                normalized=security,
                contribution=security,
            )
        )

        # --- Manipulation risk (structured analysis) ---
        from meme_tracker.risk.manipulation import analyze_manipulation

        manip_assessment = analyze_manipulation(
            holders=holders,
            liquidity=liquidity,
            activity=None,  # pipeline may pass richer activity later
        )
        manip = manip_assessment.manipulation_risk_score
        components.append(
            ScoreComponent(
                name="manipulation_assessment",
                weight=1.0,
                raw_value=manip,
                normalized=manip,
                contribution=manip,
            )
        )

        # --- Trade quality (higher = better) ---
        quality = 30.0
        if liquidity:
            max_liq = max((ls.liquidity_usd or 0.0) for ls in liquidity)
            if max_liq >= 50_000:
                quality += 35.0
            elif max_liq >= 10_000:
                quality += 20.0
            elif max_liq >= 2_000:
                quality += 10.0
        if sellability and sellability.status == SellabilityStatus.VERIFIED:
            quality += 25.0
        elif sellability and sellability.status == SellabilityStatus.LIMITED:
            quality += 10.0
        quality = min(100.0, max(0.0, quality))
        components.append(
            ScoreComponent(
                name="liquidity_and_sellability",
                weight=1.0,
                raw_value=quality,
                normalized=quality,
                contribution=quality,
            )
        )

        # --- Data confidence ---
        total_items = len(evidence) or 1
        ok_items = sum(1 for e in evidence if e.retrieval_status == "ok")
        conf = (ok_items / total_items) * 70.0
        # Bonus for having sellability + liquidity + holders
        if sellability:
            conf += 10.0
        if liquidity:
            conf += 10.0
        if holders:
            conf += 10.0
        conf = min(100.0, max(0.0, conf))
        components.append(
            ScoreComponent(
                name="evidence_completeness",
                weight=1.0,
                raw_value=conf,
                normalized=conf,
                contribution=conf,
            )
        )

        return ScoreSet(
            security_risk_score=round(security, 1),
            manipulation_risk_score=round(manip, 1),
            trade_quality_score=round(quality, 1),
            data_confidence_score=round(conf, 1),
            components=components,
        )

    def _freshness_summary(self, evidence: list[EvidenceItem]) -> dict[str, Any]:
        now = _utcnow()
        ages = []
        for e in evidence:
            if e.source_timestamp:
                ages.append((now - e.source_timestamp).total_seconds())
        return {
            "item_count": len(evidence),
            "max_age_seconds": max(ages) if ages else None,
            "avg_age_seconds": (sum(ages) / len(ages)) if ages else None,
            "stale_count": sum(
                1
                for e in evidence
                if e.freshness_threshold_seconds
                and e.source_timestamp
                and (now - e.source_timestamp).total_seconds() > e.freshness_threshold_seconds
            ),
        }


# Convenience factory used by API and worker
def create_engine() -> QualificationEngine:
    return QualificationEngine()
