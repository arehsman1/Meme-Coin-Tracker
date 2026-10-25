"""Monitoring status payload — pure assembly of known helpers."""

from meme_tracker.settings_public import build_public_settings
from meme_tracker.outcomes import summarize_outcomes
from meme_tracker.worker.wallet_jobs import clear_snapshot_cache, list_cached_snapshots


def test_monitoring_pieces_available():
    clear_snapshot_cache()
    public = build_public_settings()
    assert public["live_trading"] is False
    assert list_cached_snapshots() == []
    summary = summarize_outcomes()
    assert "sample_size" in summary
    assert summary["disclaimer"]
