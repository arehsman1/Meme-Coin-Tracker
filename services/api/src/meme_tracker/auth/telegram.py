"""
Telegram identity verification.

- Bot commands: trust only update.effective_user.id from Telegram Bot API.
- Mini App: validate initData per Telegram Web Apps docs (HMAC-SHA256).

Never trust a telegram_id supplied solely by the browser JSON body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import parse_qsl

from meme_tracker.config import get_settings

log = logging.getLogger(__name__)

# Reject initData older than this (seconds)
INIT_DATA_MAX_AGE = 86400  # 24h


class TelegramAuthError(Exception):
    pass


def parse_init_data(init_data: str) -> dict[str, str]:
    if not init_data or not isinstance(init_data, str):
        raise TelegramAuthError("initData missing")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    if "hash" not in pairs:
        raise TelegramAuthError("initData missing hash")
    return pairs


def validate_webapp_init_data(
    init_data: str,
    *,
    bot_token: Optional[str] = None,
    max_age_seconds: int = INIT_DATA_MAX_AGE,
) -> dict[str, Any]:
    """
    Official Web App validation:
    secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    calculated_hash = HMAC_SHA256(key=secret_key, msg=data_check_string)
    """
    settings = get_settings()
    token = bot_token or settings.telegram_bot_token
    if not token:
        raise TelegramAuthError("TELEGRAM_BOT_TOKEN not configured — cannot verify initData")

    pairs = parse_init_data(init_data)
    received_hash = pairs.pop("hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(
        b"WebAppData", token.encode("utf-8"), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise TelegramAuthError("initData hash mismatch")

    auth_date_s = pairs.get("auth_date")
    if auth_date_s:
        try:
            auth_date = int(auth_date_s)
        except ValueError as e:
            raise TelegramAuthError("invalid auth_date") from e
        if max_age_seconds > 0 and (time.time() - auth_date) > max_age_seconds:
            raise TelegramAuthError("initData expired")

    user: dict[str, Any] = {}
    if "user" in pairs:
        try:
            user = json.loads(pairs["user"])
        except json.JSONDecodeError as e:
            raise TelegramAuthError("invalid user JSON in initData") from e

    telegram_id = user.get("id")
    if telegram_id is not None:
        try:
            telegram_id = int(telegram_id)
        except (TypeError, ValueError) as e:
            raise TelegramAuthError("invalid user.id") from e

    return {
        "telegram_id": telegram_id,
        "user": user,
        "auth_date": int(auth_date_s) if auth_date_s else None,
        "query_id": pairs.get("query_id"),
        "start_param": pairs.get("start_param"),
        "raw_fields": pairs,
    }


def require_admin_telegram_id(telegram_id: Optional[int]) -> int:
    """
    Authorize privileged bot actions using env allowlist (TELEGRAM_ADMIN_IDS).
    Does not trust client-supplied IDs without matching the Telegram-provided sender.
    """
    if telegram_id is None:
        raise TelegramAuthError("Telegram user id missing")
    settings = get_settings()
    admins = settings.admin_telegram_ids
    if not admins:
        raise TelegramAuthError(
            "TELEGRAM_ADMIN_IDS not configured — privileged actions denied"
        )
    if int(telegram_id) not in admins:
        raise TelegramAuthError("Not an authorized administrator")
    return int(telegram_id)
