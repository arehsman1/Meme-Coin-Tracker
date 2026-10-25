"""Historical outcome collector tests — no look-ahead, honest incompleteness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meme_tracker.outcomes.collector import (
    apply_market_snapshot,
    compute_multiples,
    due_outcomes,
    list_outcomes,
    reset_outcomes,
    schedule_outcome_jobs,
    summarize_outcomes,
)


def setup_function():
    reset_outcomes()


def test_schedule_creates_six_horizons():
    ids = schedule_outcome_jobs("eval-1", "solana", "TokenMint111")
    assert len(ids) == 6
    rows = list_outcomes("eval-1")
    assert {r["horizon_minutes"] for r in rows} == {5, 15, 60, 360, 1440, 10080}
    assert all(r["outcome_status"] == "immature" for r in rows)


def test_schedule_idempotent():
    a = schedule_outcome_jobs("eval-2", "base", "0xabc")
    b = schedule_outcome_jobs("eval-2", "base", "0xabc")
    assert len(a) == 6
    assert len(list_outcomes("eval-2")) == 6
    assert set(a) == set(b)


def test_multiples_from_reference():
    m = compute_multiples(1.0, 5.5)
    assert m["reached_2x"] is True
    assert m["reached_5x"] is True
    assert m["reached_10x"] is False


def test_multiples_without_reference_all_false():
    m = compute_multiples(None, 10.0)
    assert all(v is False for v in m.values())


def test_apply_snapshot_computes_change_and_multiples():
    ids = schedule_outcome_jobs(
        "eval-3",
        "ethereum",
        "0xtoken",
        reference_price_usd=1.0,
        reference_liquidity_usd=10_000.0,
    )
    row = list_outcomes("eval-3")[0]
    apply_market_snapshot(row, price_usd=2.5, liquidity_usd=8_000.0)
    assert row["outcome_status"] == "complete"
    assert abs(row["price_change_pct"] - 150.0) < 1e-6
    assert row["multiples_reached"]["reached_2x"] is True
    assert row["multiples_reached"]["reached_5x"] is False
    assert row["route_still_available"] is True


def test_unavailable_when_no_snapshot_data():
    schedule_outcome_jobs("eval-4", "solana", "Mint")
    row = list_outcomes("eval-4")[0]
    apply_market_snapshot(row, price_usd=None, liquidity_usd=None)
    assert row["outcome_status"] == "unavailable"
    assert "no_market_snapshot" in row["limitations"]


def test_due_only_after_horizon():
    schedule_outcome_jobs("eval-5", "solana", "Mint")
    # Force one row due
    row = list_outcomes("eval-5")[0]
    row["due_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    due = due_outcomes()
    assert any(d["id"] == row["id"] for d in due)


def test_summarize_discloses_sample_and_disclaimer():
    schedule_outcome_jobs(
        "eval-6", "solana", "Mint", reference_price_usd=1.0
    )
    for row in list_outcomes("eval-6"):
        apply_market_snapshot(row, price_usd=3.0, liquidity_usd=1_000.0)
    summary = summarize_outcomes("eval-6")
    assert summary["sample_size"] == 6
    assert summary["complete"] == 6
    assert summary["multiples_hits"]["reached_2x"] == 6
    assert "not wallet" in summary["limitations"][0].lower() or "wallet" in summary["disclaimer"].lower()
