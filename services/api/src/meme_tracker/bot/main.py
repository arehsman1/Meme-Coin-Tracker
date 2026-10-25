"""
Telegram bot entrypoint.

Commands (authorization-aware):
  /start /help /status /chains /health /miniapp
  /token <chain> <address>
  /wallets /wallet /trackwallet /untrackwallet
  /outcomes [evaluation_id]
  /pausealerts /resumealerts

Public wallet tracking does not require connecting a personal wallet.
Live trading remains OFF.
Without TELEGRAM_BOT_TOKEN the process exits with a clear message.
"""

from __future__ import annotations

import logging
import os
import sys

from meme_tracker.bot.formatters import (
    HELP_TEXT,
    format_outcomes_summary,
    format_wallet_detail,
    format_wallet_list,
)
from meme_tracker.config import get_settings
from meme_tracker.models.enums import Chain
from meme_tracker.qualification.pipeline import evaluate_token

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [bot] %(message)s",
)
log = logging.getLogger("bot")

STATUS_EMOJI = {
    "QUALIFIED_TRADE_CANDIDATE": "✅",
    "REJECTED_CRITICAL_RISK": "🛑",
    "PENDING_DATA_INCOMPLETE": "⏳",
    "MONITOR_NOT_YET_QUALIFIED": "👁",
    "SECURITY_QUALIFIED_NO_TRADE_SIGNAL": "🔐",
    "QUALIFICATION_REVOKED": "⚠️",
}


def _require_token() -> str:
    settings = get_settings()
    if not settings.telegram_bot_token:
        log.error("TELEGRAM_BOT_TOKEN is not set. Bot cannot start.")
        sys.exit(1)
    return settings.telegram_bot_token


def _is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    try:
        return get_settings().is_admin(int(user_id))
    except Exception:
        return False


def build_application():
    """Build the telegram Application. Lazy import so package loads without the lib."""
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    settings = get_settings()
    token = _require_token()

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(HELP_TEXT)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await start(update, context)

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Desired: continuous scanning ON, trading OFF\n"
            "Actual: worker/DB status depends on deployment.\n"
            "Use the API /api/v1/scanner/status or Mini App for full detail."
        )

    async def chains_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Supported chains:\n• solana\n• ethereum\n• base"
        )

    async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from meme_tracker.providers.registry import get_registry

        lines = []
        for p in get_registry().all():
            try:
                resp = await p.ping()
                lines.append(
                    f"• {p.name}: {'OK' if resp.ok else 'FAIL'} ({p.health.value})"
                )
            except Exception as e:
                lines.append(f"• {p.name}: error {e}")
        await update.message.reply_text("Provider health:\n" + "\n".join(lines))

    async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = settings.mini_app_url
        if not url:
            await update.message.reply_text(
                "MINI_APP_URL is not configured. Set it to your public HTTPS Mini App URL."
            )
            return
        await update.message.reply_text(f"Open Mini App:\n{url}")

    async def token_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /token <solana|ethereum|base> <address>"
            )
            return
        chain_s, address = context.args[0].lower(), context.args[1]
        try:
            chain = Chain(chain_s)
        except ValueError:
            await update.message.reply_text("Chain must be solana, ethereum or base")
            return

        await update.message.reply_text(f"Evaluating {chain.value}:{address[:12]}…")
        try:
            decision = await evaluate_token(chain, address)
        except Exception as e:
            await update.message.reply_text(f"Evaluation error: {e}")
            return

        emoji = STATUS_EMOJI.get(decision.final_status.value, "•")
        name = decision.identity.name or "?"
        symbol = decision.identity.symbol or "?"
        text = (
            f"{emoji} {name} ({symbol})\n"
            f"Chain: {decision.chain.value}\n"
            f"Status: {decision.final_status.value}\n"
            f"Security risk: {decision.scores.security_risk_score}\n"
            f"Manipulation risk: {decision.scores.manipulation_risk_score}\n"
            f"Trade quality: {decision.scores.trade_quality_score}\n"
            f"Data confidence: {decision.scores.data_confidence_score}\n"
            f"Reasons:\n- " + "\n- ".join(decision.reasons[:5]) + "\n\n"
            "Not a guarantee of safety, sellability or profit."
        )
        await update.message.reply_text(text)

    async def wallets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from meme_tracker.wallets.repository import list_tracked_wallets, wallet_storage_mode

        chain = context.args[0].lower() if context.args else None
        try:
            rows = list_tracked_wallets(chain=chain, active_only=True, limit=50)
        except Exception as e:
            await update.message.reply_text(f"Error listing wallets: {e}")
            return
        payload = [
            {
                "chain": getattr(w.chain, "value", w.chain),
                "address": w.address,
                "label": w.label,
                "is_active": w.is_active,
            }
            for w in rows
        ]
        await update.message.reply_text(
            format_wallet_list(payload, storage=wallet_storage_mode())
        )

    async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /wallet <solana|ethereum|base> <address>"
            )
            return
        chain_s, address = context.args[0].lower(), context.args[1]
        try:
            Chain(chain_s)
        except ValueError:
            await update.message.reply_text("Chain must be solana, ethereum or base")
            return

        from meme_tracker.wallets.ledger import compute_performance, snapshot_to_dict
        from meme_tracker.wallets.repository import (
            get_tracked_wallet,
            get_wallet_profile,
            list_wallet_transactions,
            wallet_storage_mode,
        )

        tracked = get_tracked_wallet(chain_s, address)
        profile = get_wallet_profile(chain_s, address)
        txs = list_wallet_transactions(chain=chain_s, wallet_address=address, limit=200)
        snap = compute_performance(chain_s, address, txs)
        perf = snapshot_to_dict(snap)

        tracked_d = None
        if tracked:
            tracked_d = {
                "chain": getattr(tracked.chain, "value", tracked.chain),
                "address": tracked.address,
                "label": tracked.label,
                "is_active": tracked.is_active,
            }
        profile_d = None
        if profile:
            profile_d = {
                "observed_trade_count": profile.observed_trade_count,
                "observed_significant_count": profile.observed_significant_count,
                "data_completeness_score": profile.data_completeness_score,
                "confidence_score": profile.confidence_score,
            }

        await update.message.reply_text(
            format_wallet_detail(
                tracked_d, profile_d, perf, storage=wallet_storage_mode()
            )
        )

    async def trackwallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if not _is_admin(user_id):
            await update.message.reply_text(
                "Admin only. Public list remains available via /wallets."
            )
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /trackwallet <chain> <address> [label]"
            )
            return
        chain_s, address = context.args[0].lower(), context.args[1]
        label = " ".join(context.args[2:]) if len(context.args) > 2 else None
        try:
            Chain(chain_s)
        except ValueError:
            await update.message.reply_text("Chain must be solana, ethereum or base")
            return

        from meme_tracker.models.enums import WalletSource
        from meme_tracker.wallets.models import TrackedWalletCreate
        from meme_tracker.wallets.repository import upsert_tracked_wallet, wallet_storage_mode

        try:
            row = upsert_tracked_wallet(
                TrackedWalletCreate(
                    chain=Chain(chain_s),
                    address=address,
                    label=label,
                    source=WalletSource.MANUAL,
                )
            )
        except Exception as e:
            await update.message.reply_text(f"Track failed: {e}")
            return
        await update.message.reply_text(
            f"Tracking {getattr(row.chain, 'value', row.chain)}:{row.address}\n"
            f"storage: {wallet_storage_mode()}\n"
            "Public monitoring only — no personal wallet connected."
        )

    async def untrackwallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if not _is_admin(user_id):
            await update.message.reply_text("Admin only.")
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /untrackwallet <chain> <address>"
            )
            return
        chain_s, address = context.args[0].lower(), context.args[1]
        from meme_tracker.wallets.repository import deactivate_tracked_wallet

        ok = deactivate_tracked_wallet(chain_s, address)
        await update.message.reply_text(
            "Untracked (history kept)." if ok else "Wallet not found or already inactive."
        )

    async def outcomes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from meme_tracker.outcomes import summarize_outcomes

        eval_id = context.args[0] if context.args else None
        summary = summarize_outcomes(eval_id)
        await update.message.reply_text(format_outcomes_summary(summary))

    async def pause_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Alert preference stored only when Supabase is connected. (stub)"
        )

    async def resume_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Alert preference stored only when Supabase is connected. (stub)"
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("chains", chains_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(CommandHandler("miniapp", miniapp_cmd))
    app.add_handler(CommandHandler("token", token_cmd))
    app.add_handler(CommandHandler("wallets", wallets_cmd))
    app.add_handler(CommandHandler("wallet", wallet_cmd))
    app.add_handler(CommandHandler("trackwallet", trackwallet_cmd))
    app.add_handler(CommandHandler("untrackwallet", untrackwallet_cmd))
    app.add_handler(CommandHandler("outcomes", outcomes_cmd))
    app.add_handler(CommandHandler("pausealerts", pause_alerts))
    app.add_handler(CommandHandler("resumealerts", resume_alerts))
    return app


def main():
    _require_token()
    try:
        app = build_application()
    except Exception as e:
        log.error("Failed to build bot application: %s", e)
        sys.exit(1)
    log.info("Starting Telegram bot polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
