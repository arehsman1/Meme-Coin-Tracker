"""
Background worker entrypoint.

Loops:
- heartbeat
- provider health
- job claim/process (evaluate) with failure backoff
- notification outbox
- new-token discovery (DexScreener)
- historical outcome collection
- wallet performance snapshots

Without SUPABASE uses in-memory stores. Never fabricates results.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys

from meme_tracker import ENGINE_VERSION, __version__
from meme_tracker.config import get_settings
from meme_tracker.db import (
    claim_jobs,
    claim_pending_outbox,
    complete_job,
    create_job,
    mark_outbox_sent,
    storage_mode,
    upsert_token_and_evaluation,
)
from meme_tracker.discovery import run_discovery_cycle
from meme_tracker.models.enums import Chain
from meme_tracker.notifications.outbox import (
    format_qualification_telegram_text,
    format_wallet_significant_telegram_text,
)
from meme_tracker.monitoring.revocation import (
    format_revocation_telegram_text,
    run_revocation_cycle,
)
from meme_tracker.outcomes import collect_due
from meme_tracker.providers.registry import get_registry
from meme_tracker.qualification.pipeline import evaluate_token

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
log = logging.getLogger("worker")

_shutdown = asyncio.Event()
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"

# Intervals (seconds) — tunable via env later
DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "180"))
OUTCOME_INTERVAL = int(os.getenv("OUTCOME_INTERVAL_SECONDS", "60"))
JOB_INTERVAL = int(os.getenv("JOB_INTERVAL_SECONDS", "5"))
OUTBOX_INTERVAL = int(os.getenv("OUTBOX_INTERVAL_SECONDS", "10"))
REVOCATION_INTERVAL = int(os.getenv("REVOCATION_INTERVAL_SECONDS", "300"))
WALLET_PERF_INTERVAL = int(os.getenv("WALLET_PERF_INTERVAL_SECONDS", "300"))

from meme_tracker.worker.recovery import FailureTracker

_job_failures = FailureTracker()


def _handle_signal(*_args):
    log.info("Shutdown signal received")
    _shutdown.set()


async def heartbeat_loop(interval: int = 30):
    while not _shutdown.is_set():
        log.debug("heartbeat worker_id=%s storage=%s", WORKER_ID, storage_mode())
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def provider_health_loop(interval: int = 120):
    registry = get_registry()
    while not _shutdown.is_set():
        for p in registry.all():
            try:
                resp = await p.ping()
                log.info(
                    "provider %s health=%s ok=%s latency=%s",
                    p.name,
                    p.health.value,
                    resp.ok,
                    resp.latency_ms,
                )
            except Exception as e:
                log.warning("provider %s ping failed: %s", p.name, e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def process_evaluate_job(job: dict) -> None:
    chain_s = job.get("chain")
    address = job.get("token_address") or (job.get("payload") or {}).get("address")
    if not chain_s or not address:
        complete_job(job["id"], error="missing chain or address")
        return
    try:
        chain = Chain(chain_s.lower())
    except ValueError:
        complete_job(job["id"], error=f"bad chain {chain_s}")
        return

    log.info("evaluate job %s %s:%s", job["id"], chain.value, address[:12])
    try:
        decision = await evaluate_token(chain, address)
        result = upsert_token_and_evaluation(decision)
        log.info(
            "job %s done status=%s storage=%s",
            job["id"],
            decision.final_status.value,
            result.get("storage"),
        )
        complete_job(job["id"])
    except Exception as e:
        log.exception("job %s failed", job["id"])
        complete_job(job["id"], error=str(e))
        raise


async def job_loop(interval: int = JOB_INTERVAL):
    while not _shutdown.is_set():
        try:
            jobs = claim_jobs(WORKER_ID, limit=3, lease_seconds=180)
            for job in jobs:
                if _job_failures.should_skip(job):
                    log.warning(
                        "skipping job %s due to failure backoff %s",
                        job.get("id"),
                        _job_failures.stats(),
                    )
                    continue
                jtype = job.get("job_type") or ""
                try:
                    if jtype in ("evaluate", "reevaluate", "token_evaluate"):
                        await process_evaluate_job(job)
                    elif jtype == "discovery":
                        await run_discovery_cycle(max_per_chain=8)
                        complete_job(job["id"])
                    else:
                        log.warning("unknown job type %s", jtype)
                        complete_job(job["id"], error=f"unknown type {jtype}")
                    _job_failures.record_success(job)
                except Exception as e:
                    rec = _job_failures.record_failure(job, str(e))
                    log.error(
                        "job %s failed attempt=%s cooldown_until=%s: %s",
                        job.get("id"),
                        rec.count,
                        rec.cooldown_until,
                        e,
                    )
                    try:
                        complete_job(job["id"], error=str(e))
                    except Exception:
                        pass
        except Exception as e:
            log.error("job_loop error: %s", e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def wallet_performance_loop(interval: int = WALLET_PERF_INTERVAL):
    """Recompute observed performance for active tracked wallets."""
    from meme_tracker.worker.wallet_jobs import run_wallet_performance_cycle

    try:
        await asyncio.wait_for(_shutdown.wait(), timeout=20)
        return
    except asyncio.TimeoutError:
        pass
    while not _shutdown.is_set():
        try:
            run_wallet_performance_cycle()
        except Exception as e:
            log.error("wallet_performance_loop error: %s", e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def discovery_loop(interval: int = DISCOVERY_INTERVAL):
    """Periodic new-token discovery. First run after short delay."""
    try:
        await asyncio.wait_for(_shutdown.wait(), timeout=15)
        return
    except asyncio.TimeoutError:
        pass
    while not _shutdown.is_set():
        try:
            summary = await run_discovery_cycle(max_per_chain=8)
            log.info(
                "discovery enqueued=%s candidates=%s",
                len(summary.get("enqueued") or []),
                summary.get("candidates"),
            )
        except Exception as e:
            log.error("discovery_loop error: %s", e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def outcome_loop(interval: int = OUTCOME_INTERVAL):
    while not _shutdown.is_set():
        try:
            n = await collect_due(limit=15)
            if n:
                log.info("collected %s due outcomes", n)
        except Exception as e:
            log.error("outcome_loop error: %s", e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue



async def revocation_loop(interval: int = REVOCATION_INTERVAL):
    """Re-evaluate qualified/monitor tokens; revoke on material risk."""
    while not _shutdown.is_set():
        try:
            summary = await run_revocation_cycle(limit=10)
            if summary.get("checked"):
                log.info(
                    "revocation cycle checked=%s revoked=%s",
                    summary.get("checked"),
                    summary.get("revoked"),
                )
        except Exception as e:
            log.error("revocation_loop error: %s", e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def outbox_loop(interval: int = OUTBOX_INTERVAL):
    settings = get_settings()
    while not _shutdown.is_set():
        try:
            messages = claim_pending_outbox(limit=5)
            for msg in messages:
                oid = msg.get("id")
                ntype = msg.get("notification_type")
                payload = msg.get("payload") or {}
                if ntype in (
                    "qualification",
                    "risk_revocation",
                    "wallet_significant_swap",
                ):
                    if ntype == "risk_revocation":
                        text = format_revocation_telegram_text(payload)
                    elif ntype == "wallet_significant_swap":
                        text = format_wallet_significant_telegram_text(payload)
                    else:
                        text = format_qualification_telegram_text(payload)
                    log.info("OUTBOX %s alert:\n%s", ntype, text[:400])
                    if not settings.telegram_bot_token:
                        mark_outbox_sent(
                            oid,
                            success=False,
                            error="TELEGRAM_BOT_TOKEN not set — alert logged only",
                        )
                    else:
                        try:
                            from telegram import Bot

                            bot = Bot(token=settings.telegram_bot_token)
                            recipients = []
                            if msg.get("recipient_telegram_id"):
                                recipients = [int(msg["recipient_telegram_id"])]
                            else:
                                recipients = list(settings.admin_telegram_ids)
                            if not recipients:
                                mark_outbox_sent(
                                    oid,
                                    success=False,
                                    error="No recipient or TELEGRAM_ADMIN_IDS",
                                )
                            else:
                                for rid in recipients:
                                    await bot.send_message(chat_id=rid, text=text)
                                mark_outbox_sent(oid, success=True)
                        except ImportError:
                            mark_outbox_sent(
                                oid, success=False, error="python-telegram-bot not installed"
                            )
                        except Exception as e:
                            mark_outbox_sent(oid, success=False, error=str(e))
                else:
                    mark_outbox_sent(oid, success=False, error=f"unknown type {ntype}")
        except Exception as e:
            log.error("outbox_loop error: %s", e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def run():
    settings = get_settings()
    log.info(
        "Worker starting version=%s engine=%s storage=%s worker_id=%s discovery_interval=%s",
        __version__,
        ENGINE_VERSION,
        storage_mode(),
        WORKER_ID,
        DISCOVERY_INTERVAL,
    )
    if storage_mode() == "memory":
        log.warning(
            "In-memory store active. Configure SUPABASE_* for durable persistence."
        )

    get_registry()

    tasks = [
        asyncio.create_task(heartbeat_loop()),
        asyncio.create_task(provider_health_loop()),
        asyncio.create_task(job_loop()),
        asyncio.create_task(discovery_loop()),
        asyncio.create_task(outcome_loop()),
        asyncio.create_task(revocation_loop()),
        asyncio.create_task(outbox_loop()),
        asyncio.create_task(wallet_performance_loop()),
    ]

    await _shutdown.wait()
    log.info("Shutting down…")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await get_registry().close_all()
    log.info("Worker stopped")


def enqueue_evaluate(chain: str, address: str) -> str:
    return create_job(
        "evaluate",
        chain=chain,
        token_address=address,
        payload={"address": address},
    )


def main():
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
