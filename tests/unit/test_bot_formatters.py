"""Bot message formatters — no Telegram network required."""

from meme_tracker.bot.formatters import (
    HELP_TEXT,
    format_outcomes_summary,
    format_wallet_detail,
    format_wallet_list,
    short_addr,
)


def test_short_addr():
    assert "…" in short_addr("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert short_addr("short") == "short"


def test_format_wallet_list_empty():
    text = format_wallet_list([], storage="memory")
    assert "No tracked" in text
    assert "trackwallet" in text


def test_format_wallet_list_rows():
    text = format_wallet_list(
        [{"chain": "solana", "address": "ABCDEFGHIJKLMNOP", "label": "Whale"}],
        storage="memory",
    )
    assert "Whale" in text
    assert "solana" in text


def test_format_wallet_detail_with_perf():
    text = format_wallet_detail(
        {"chain": "base", "address": "0xabc", "label": "A", "is_active": True},
        {"observed_trade_count": 3, "observed_significant_count": 1,
         "data_completeness_score": 50, "confidence_score": 40},
        {
            "sample_size": 3,
            "closed_trade_count": 1,
            "win_rate": 1.0,
            "realized_pnl_usd": 12.5,
            "realized_pnl_status": "KNOWN",
            "data_completeness_score": 50,
            "confidence_score": 40,
            "limitations": ["small_sample"],
        },
        storage="memory",
    )
    assert "Performance" in text
    assert "small_sample" in text
    assert "Not lifetime" in text or "financial advice" in text.lower()


def test_outcomes_summary_disclaimer():
    text = format_outcomes_summary(
        {
            "sample_size": 6,
            "complete": 2,
            "unavailable": 1,
            "immature": 3,
            "multiples_hits": {"reached_2x": 1},
            "avg_price_change_pct": 12.5,
            "disclaimer": "Observed market snapshots only.",
        }
    )
    assert "Sample: 6" in text
    assert "reached_2x=1" in text
    assert "wallet profit" in text.lower() or "snapshots" in text.lower()


def test_help_mentions_wallets_and_trading_off():
    assert "/wallets" in HELP_TEXT
    assert "OFF" in HELP_TEXT
