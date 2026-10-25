"""Unit tests for the authoritative qualification engine."""

from datetime import datetime, timezone

import pytest

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
    SellabilityEvidence,
    TokenIdentity,
)
from meme_tracker.qualification.engine import QualificationEngine


def _identity(chain: Chain = Chain.SOLANA, address: str = "So11111111111111111111111111111111111111112") -> TokenIdentity:
    return TokenIdentity(chain=chain, address=address, name="Test", symbol="TEST")


def _evidence(key: str = "basic", status: str = "ok") -> list[EvidenceItem]:
    return [
        EvidenceItem(
            key=key,
            value=True,
            source="test",
            observed_at=datetime.now(timezone.utc),
            retrieval_status=status,
        )
    ]


def _pass_checks() -> list[CheckOutcome]:
    return [
        CheckOutcome(
            check_id="identity_valid",
            name="Identity",
            result=CheckResult.PASS,
            severity="critical",
            reason="Valid",
            is_required=True,
        ),
        CheckOutcome(
            check_id="mint_authority_renounced_or_safe",
            name="Mint authority",
            result=CheckResult.PASS,
            severity="critical",
            reason="Renounced",
            is_required=True,
        ),
        CheckOutcome(
            check_id="freeze_authority_safe",
            name="Freeze authority",
            result=CheckResult.PASS,
            severity="critical",
            reason="None",
            is_required=True,
        ),
        CheckOutcome(
            check_id="no_blacklist_or_pause_trap",
            name="No blacklist/pause",
            result=CheckResult.PASS,
            severity="critical",
            reason="Clean",
            is_required=True,
        ),
        CheckOutcome(
            check_id="sellability_not_failed",
            name="Sellability",
            result=CheckResult.PASS,
            severity="critical",
            reason="OK",
            is_required=True,
        ),
        CheckOutcome(
            check_id="liquidity_present",
            name="Liquidity",
            result=CheckResult.PASS,
            severity="critical",
            reason="Present",
            is_required=True,
        ),
    ]


def test_critical_fail_rejects():
    engine = QualificationEngine()
    checks = _pass_checks()
    checks[1] = CheckOutcome(
        check_id="mint_authority_renounced_or_safe",
        name="Mint authority",
        result=CheckResult.FAIL,
        severity="critical",
        reason="Mint still active",
        is_required=True,
    )
    decision = engine.evaluate(
        identity=_identity(),
        evidence=_evidence(),
        checks=checks,
    )
    assert decision.final_status == QualificationStatus.REJECTED_CRITICAL_RISK
    assert any("CRITICAL FAIL" in r for r in decision.reasons)


def test_sellability_failed_rejects():
    engine = QualificationEngine()
    decision = engine.evaluate(
        identity=_identity(),
        evidence=_evidence(),
        checks=_pass_checks(),
        sellability=SellabilityEvidence(
            status=SellabilityStatus.FAILED,
            method="simulation",
            limitations=["Transfer reverted"],
        ),
    )
    assert decision.final_status == QualificationStatus.REJECTED_CRITICAL_RISK


def test_unknown_critical_pending():
    engine = QualificationEngine()
    checks = _pass_checks()
    checks[0] = CheckOutcome(
        check_id="identity_valid",
        name="Identity",
        result=CheckResult.UNKNOWN,
        severity="critical",
        reason="Could not verify",
        is_required=True,
    )
    decision = engine.evaluate(
        identity=_identity(),
        evidence=_evidence(),
        checks=checks,
    )
    assert decision.final_status == QualificationStatus.PENDING_DATA_INCOMPLETE


def test_low_confidence_pending():
    engine = QualificationEngine()
    decision = engine.evaluate(
        identity=_identity(),
        evidence=_evidence(status="error"),  # retrieval failed → low confidence
        checks=_pass_checks(),
    )
    # With only errored evidence, confidence is low
    assert decision.final_status in (
        QualificationStatus.PENDING_DATA_INCOMPLETE,
        QualificationStatus.MONITOR_NOT_YET_QUALIFIED,
    )


def test_happy_path_qualifies():
    engine = QualificationEngine()
    evidence = [
        EvidenceItem(
            key="mint",
            value="ok",
            source="rpc",
            observed_at=datetime.now(timezone.utc),
            retrieval_status="ok",
        ),
        EvidenceItem(
            key="liquidity",
            value=50000,
            source="dexscreener",
            observed_at=datetime.now(timezone.utc),
            retrieval_status="ok",
        ),
        EvidenceItem(
            key="holders",
            value=100,
            source="rpc",
            observed_at=datetime.now(timezone.utc),
            retrieval_status="ok",
        ),
    ]
    decision = engine.evaluate(
        identity=_identity(),
        evidence=evidence,
        checks=_pass_checks(),
        sellability=SellabilityEvidence(
            status=SellabilityStatus.VERIFIED,
            method="observed_tx",
        ),
        liquidity=[
            LiquiditySnapshot(
                liquidity_usd=75_000,
                observed_at=datetime.now(timezone.utc),
                source="dexscreener",
            )
        ],
        holders=HolderSnapshot(
            top_holder_pct=8.0,
            creator_pct=2.0,
            unique_holders=400,
            observed_at=datetime.now(timezone.utc),
            source="rpc",
        ),
    )
    assert decision.final_status == QualificationStatus.QUALIFIED_TRADE_CANDIDATE
    assert decision.scores.security_risk_score < 40
    assert decision.scores.data_confidence_score >= 55


def test_reproducibility():
    engine = QualificationEngine()
    identity = _identity()
    checks = _pass_checks()
    evidence = _evidence()
    d1 = engine.evaluate(identity=identity, evidence=evidence, checks=checks, evaluation_id="fixed-id")
    d2 = engine.evaluate(identity=identity, evidence=evidence, checks=checks, evaluation_id="fixed-id")
    assert d1.final_status == d2.final_status
    assert d1.scores.security_risk_score == d2.scores.security_risk_score
    assert d1.policy_version == d2.policy_version
