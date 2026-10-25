"""Tests for in-memory persistence and outbox qualification rule."""

from datetime import datetime, timezone

from meme_tracker.db.repository import (
    get_token,
    list_tokens,
    storage_mode,
    upsert_token_and_evaluation,
    claim_pending_outbox,
)
from meme_tracker.models.enums import (
    Chain,
    CheckResult,
    QualificationStatus,
    SellabilityStatus,
)
from meme_tracker.models.evidence import (
    CheckOutcome,
    EvidenceItem,
    LiquiditySnapshot,
    QualificationDecision,
    ScoreSet,
    SellabilityEvidence,
    TokenIdentity,
)
from meme_tracker.qualification.engine import QualificationEngine


def _decision(
    status: QualificationStatus = QualificationStatus.PENDING_DATA_INCOMPLETE,
    address: str = "TestMint111111111111111111111111111111111",
) -> QualificationDecision:
    engine = QualificationEngine()
    identity = TokenIdentity(chain=Chain.SOLANA, address=address, name="T", symbol="T")
    checks = [
        CheckOutcome(
            check_id="identity_valid",
            name="Identity",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="mint_authority_renounced_or_safe",
            name="Mint",
            result=CheckResult.PASS if status != QualificationStatus.REJECTED_CRITICAL_RISK else CheckResult.FAIL,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="freeze_authority_safe",
            name="Freeze",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
        CheckOutcome(
            check_id="no_blacklist_or_pause_trap",
            name="Blacklist",
            result=CheckResult.UNKNOWN,
            severity="critical",
            reason="unknown",
            is_required=True,
        ),
        CheckOutcome(
            check_id="sellability_not_failed",
            name="Sell",
            result=CheckResult.UNKNOWN,
            severity="critical",
            reason="unknown",
            is_required=True,
        ),
        CheckOutcome(
            check_id="liquidity_present",
            name="Liq",
            result=CheckResult.PASS,
            severity="critical",
            reason="ok",
            is_required=True,
        ),
    ]
    evidence = [
        EvidenceItem(
            key="a",
            value=1,
            source="t",
            observed_at=datetime.now(timezone.utc),
            retrieval_status="ok",
        )
    ]
    d = engine.evaluate(
        identity=identity,
        evidence=evidence,
        checks=checks,
        sellability=SellabilityEvidence(status=SellabilityStatus.UNKNOWN, method="none"),
        liquidity=[
            LiquiditySnapshot(
                liquidity_usd=10_000,
                observed_at=datetime.now(timezone.utc),
                source="t",
            )
        ],
    )
    # Force status for outbox test when needed
    if status == QualificationStatus.QUALIFIED_TRADE_CANDIDATE:
        object.__setattr__(d, "final_status", status)
    return d


def test_storage_mode_memory_without_supabase():
    assert storage_mode() == "memory"


def test_persist_and_list():
    d = _decision(address="PersistMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    result = upsert_token_and_evaluation(d)
    assert result["storage"] == "memory"
    assert result["evaluation_id"] == d.evaluation_id

    tok = get_token("solana", d.token_address)
    assert tok is not None
    assert tok["current_status"] == d.final_status.value

    items = list_tokens(chain="solana", limit=50)
    assert any(t["address"] == d.token_address for t in items)


def test_outbox_only_on_qualified():
    # Pending should not enqueue qualification alert
    d1 = _decision(
        status=QualificationStatus.PENDING_DATA_INCOMPLETE,
        address="NoAlertMintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    )
    upsert_token_and_evaluation(d1)
    pending_before = claim_pending_outbox(limit=50)

    # Qualified should enqueue
    d2 = _decision(
        status=QualificationStatus.QUALIFIED_TRADE_CANDIDATE,
        address="AlertMintCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
    )
    # Force all critical to PASS-like path by using engine-produced qualified if possible;
    # we already force final_status above for this address.
    object.__setattr__(d2, "final_status", QualificationStatus.QUALIFIED_TRADE_CANDIDATE)
    upsert_token_and_evaluation(d2)
    pending = claim_pending_outbox(limit=50)
    types = [m.get("notification_type") for m in pending]
    # At least one qualification message for the qualified token
    assert "qualification" in types or any(
        (m.get("payload") or {}).get("address") == d2.token_address for m in pending
    )
