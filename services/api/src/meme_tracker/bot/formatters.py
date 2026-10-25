"""Pure text formatters for Telegram messages — unit-testable without the Telegram SDK."""

from __future__ import annotations

from typing import Any


def short_addr(addr: str, n: int = 6) -> str:
    if not addr or len(addr) <= n * 2 + 2:
        return addr or ""
    prefix = 2 if addr.startswith("0x") else 0
    return f"{addr[: n + prefix]}…{addr[-n:]}"


def format_wallet_list(wallets: list[dict[str, Any]], storage: str = "?") -> str:
    if not wallets:
        return (
            "No tracked public wallets.\n"
            "Use /trackwallet <chain> <address> [label]\n"
            f"storage: {storage}"
        )
    lines = [f"Tracked wallets ({len(wallets)}) · storage: {storage}"]
    for w in wallets[:20]:
        label = w.get("label") or short_addr(str(w.get("address", "")))
        chain = w.get("chain", "?")
        lines.append(f"• [{chain}] {label}")
    if len(wallets) > 20:
        lines.append(f"… and {len(wallets) - 20} more")
    lines.append("\n/wallet <chain> <address> for detail")
    return "\n".join(lines)


def format_wallet_detail(
    tracked: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    perf: dict[str, Any] | None,
    storage: str = "?",
) -> str:
    lines: list[str] = []
    if tracked:
        lines.append(tracked.get("label") or "Wallet")
        lines.append(f"Chain: {tracked.get('chain')}")
        lines.append(f"Address: {tracked.get('address')}")
        lines.append(
            f"Status: {'active' if tracked.get('is_active', True) else 'inactive'}"
        )
    else:
        lines.append("Wallet not in tracked list (performance may still be available)")
    if profile:
        lines.append(
            f"Observed swaps: {profile.get('observed_trade_count', 0)} · "
            f"significant: {profile.get('observed_significant_count', 0)}"
        )
        lines.append(
            f"Completeness: {profile.get('data_completeness_score', 0)}/100 · "
            f"confidence: {profile.get('confidence_score', 0)}/100"
        )
    if perf:
        wr = perf.get("win_rate")
        wr_s = f"{wr * 100:.1f}%" if isinstance(wr, (int, float)) else "—"
        pnl = perf.get("realized_pnl_usd")
        pnl_s = f"${pnl:,.2f}" if isinstance(pnl, (int, float)) else "—"
        lines.append("")
        lines.append("Performance (observed swaps only)")
        lines.append(f"Sample: {perf.get('sample_size', 0)} · closed: {perf.get('closed_trade_count', 0)}")
        lines.append(f"Win rate: {wr_s} · realized PnL: {pnl_s} ({perf.get('realized_pnl_status', '?')})")
        lines.append(
            f"Completeness: {perf.get('data_completeness_score', 0)}/100 · "
            f"confidence: {perf.get('confidence_score', 0)}/100"
        )
        lim = perf.get("limitations") or []
        if lim:
            lines.append("Limitations: " + "; ".join(str(x) for x in lim[:4]))
        lines.append(
            "Not lifetime P&L. Incomplete history is disclosed. Not financial advice."
        )
    lines.append(f"storage: {storage}")
    return "\n".join(lines)


def format_outcomes_summary(summary: dict[str, Any]) -> str:
    lines = [
        "Historical outcomes summary",
        f"Sample: {summary.get('sample_size', 0)} · "
        f"complete: {summary.get('complete', 0)} · "
        f"unavailable: {summary.get('unavailable', 0)} · "
        f"immature: {summary.get('immature', 0)}",
    ]
    hits = summary.get("multiples_hits") or {}
    if hits:
        parts = [f"{k}={v}" for k, v in hits.items()]
        lines.append("Token price multiples (not wallet profit): " + ", ".join(parts))
    avg = summary.get("avg_price_change_pct")
    if avg is not None:
        lines.append(f"Avg price change (complete rows): {avg:.2f}%")
    lines.append(summary.get("disclaimer") or "Observed market snapshots only.")
    return "\n".join(lines)


HELP_TEXT = (
    "Meme Coin Tracker bot\n\n"
    "Token:\n"
    "/token <chain> <address> — evaluate a token\n\n"
    "Public wallets (no personal wallet required):\n"
    "/wallets — list tracked wallets\n"
    "/wallet <chain> <address> — profile + performance\n"
    "/trackwallet <chain> <address> [label] — start tracking (admin)\n"
    "/untrackwallet <chain> <address> — stop tracking (admin)\n\n"
    "Outcomes:\n"
    "/outcomes [evaluation_id] — historical snapshot summary\n\n"
    "Service:\n"
    "/status /chains /health /miniapp\n"
    "/pausealerts /resumealerts\n\n"
    "Live trading is OFF. Assessments are not financial advice."
)
