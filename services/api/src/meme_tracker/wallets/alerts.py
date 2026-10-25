"""Wallet activity → notification outbox.

Wallet alerts are independent of token qualification.
A tracked wallet buying a REJECTED token still generates a wallet alert
(with the token's current status clearly labelled).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from meme_tracker.models.enums import WalletEventType
from meme_tracker.notifications.outbox import (
    OutboxMessage,
    build_wallet_significant_alert_payload,
    should_send_wallet_significant_alert,
)
from meme_tracker.wallets.models import WalletTransaction

log = logging.getLogger(__name__)


def _lookup_token_status(chain: str, token_address: str | None) -> str | None:
    if not token_address:
        return None
    try:
        from meme_tracker.db import get_token

        row = get_token(chain, token_address)
        if row:
            return row.get("current_status")
    except Exception as e:
        log.debug("token status lookup failed: %s", e)
    return None


def _lookup_wallet_label(chain: str, wallet_address: str) -> str | None:
    try:
        from meme_tracker.wallets.repository import get_tracked_wallet

        w = get_tracked_wallet(chain, wallet_address)
        if w:
            return w.label
    except Exception:
        pass
    return None


def maybe_enqueue_wallet_alert(
    tx: WalletTransaction,
    *,
    threshold_usd: float | None = None,
) -> Optional[str]:
    """
    If the transaction qualifies as a significant swap alert, enqueue outbox message.
    Returns outbox message id or None.
    Never raises — alert failures must not block ingestion.
    """
    event_type = (
        tx.event_type.value if hasattr(tx.event_type, "value") else str(tx.event_type)
    )
    usd_status = (
        tx.value_usd_status.value
        if hasattr(tx.value_usd_status, "value")
        else str(tx.value_usd_status)
    )

    if not should_send_wallet_significant_alert(
        event_type=event_type,
        is_significant=bool(tx.is_significant),
        value_usd_status=usd_status,
    ):
        return None

    chain = tx.chain.value if hasattr(tx.chain, "value") else str(tx.chain)
    token_status = _lookup_token_status(chain, tx.token_address)
    label = _lookup_wallet_label(chain, tx.wallet_address)

    payload = build_wallet_significant_alert_payload(
        chain=chain,
        wallet_address=tx.wallet_address,
        event_type=event_type,
        tx_signature=tx.tx_signature,
        value_usd=tx.value_usd,
        value_usd_status=usd_status,
        token_address=tx.token_address,
        token_qualification_status=token_status,
        wallet_label=label,
        dex_or_protocol=tx.dex_or_protocol,
        threshold_usd=threshold_usd,
    )

    # Dedupe: one alert per chain+tx+wallet+event
    dedupe = f"wallet_sig:{chain}:{tx.tx_signature}:{tx.wallet_address}:{event_type}"
    msg = OutboxMessage(
        notification_type="wallet_significant_swap",
        payload=payload,
        dedupe_key=dedupe,
    )

    try:
        from meme_tracker.db import enqueue_outbox

        oid = enqueue_outbox(msg)
        log.info(
            "Enqueued wallet_significant_swap alert id=%s wallet=%s value=%s token_status=%s",
            oid,
            tx.wallet_address[:12],
            tx.value_usd,
            token_status,
        )
        return oid
    except Exception as e:
        log.error("Failed to enqueue wallet alert: %s", e)
        return None
