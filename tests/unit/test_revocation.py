"""Unit tests for revocation decision logic (no network)."""

from datetime import datetime, timezone
from uuid import uuid4

from meme_tracker.models.enums import (
    Chain,
    CheckResult,
    QualificationStatus,
    SellabilityStatus,
)
from meme_tracker.models.evidence import (
    CheckOutcome,
    QualificationDecision,
    ScoreSet,
    SellabilityEvidence,
    TokenIdentity,
)
from meme_tracker.monitoring.revocation import (
    material_risk_reasons,
    should_revoke,
)


def _decision(
    *,
    status: QualificationStatus,
    checks: list[CheckOutcome] | None = None,
    sellability: SellabilityEvidence | None = None,
) -> QualificationDecision:
    return QualificationDecision(
        evaluation_id=str(uuid4()),
        chain=Chain.SOLANA,
        token_address="So11111111111111111111111111111111111111112",
        policy_version="test",
        engine_version="test",
        identity=TokenIdentity(chain=Chain.SOLANA, address="So11111111111111111111111111111111111111112"),
        checks=checks or [],
        scores=ScoreSet(
            security_risk_score=10,
            manipulation_risk_score=10,
            trade_quality_score=50,
            data_confidence_score=80,
        ),
        final_status=status,
        reasons=["test"],
        evaluated_at=datetime.now(timezone.utc),
        sellability=sellability,
    )


def test_no_revoke_when_not_monitored():
    d = _decision(status=QualificationStatus.PENDING_DATA_INCOMPLETE)
    assert should_revoke("PENDING_DATA_INCOMPLETE", d) is False


def test_revoke_on_critical_fail_from_qualified():
    checks = [
        CheckOutcome(
            check_id="mint_authority_renounced_or_safe",
            name="Mint",
            result=CheckResult.FAIL,
            reason="mint back",
            severity="critical",
            is_required=True,
        )
    ]
    d = _decision(
        status=QualificationStatus.REJECTED_CRITICAL_RISK,
        checks=checks,
    )
    assert should_revoke("QUALIFIED_TRADE_CANDIDATE", d) is True
    assert material_risk_reasons(d)


def test_revoke_on_sellability_failed():
    sell = SellabilityEvidence(status=SellabilityStatus.FAILED, method="test")
    d = _decision(
        status=QualificationStatus.REJECTED_CRITICAL_RISK,
        sellability=sell,
    )
    assert should_revoke("SECURITY_QUALIFIED_NO_TRADE_SIGNAL", d) is True


def test_no_revoke_when_still_healthy():
    checks = [
        CheckOutcome(
            check_id="identity_valid",
            name="Identity",
            result=CheckResult.PASS,
            reason="ok",
            severity="critical",
            is_required=True,
        )
    ]
    d = _decision(
        status=QualificationStatus.QUALIFIED_TRADE_CANDIDATE,
        checks=checks,
        sellability=SellabilityEvidence(status=SellabilityStatus.VERIFIED, method="test"),
    )
    assert should_revoke("QUALIFIED_TRADE_CANDIDATE", d) is False
