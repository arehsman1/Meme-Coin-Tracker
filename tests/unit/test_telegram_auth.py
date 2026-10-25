"""Telegram Web App initData HMAC validation tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from meme_tracker.auth.telegram import (
    TelegramAuthError,
    require_admin_telegram_id,
    validate_webapp_init_data,
)


def _make_init_data(bot_token: str, user: dict, auth_date: int | None = None) -> str:
    auth_date = auth_date if auth_date is not None else int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    fields["hash"] = calculated
    return urlencode(fields)


def test_valid_init_data(monkeypatch):
    token = "123456:ABC-TEST-TOKEN"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    # clear settings cache
    from meme_tracker.config import get_settings

    get_settings.cache_clear()

    init = _make_init_data(token, {"id": 42, "first_name": "Ada"})
    result = validate_webapp_init_data(init, bot_token=token)
    assert result["telegram_id"] == 42
    assert result["user"]["first_name"] == "Ada"


def test_tampered_hash(monkeypatch):
    token = "123456:ABC-TEST-TOKEN"
    init = _make_init_data(token, {"id": 1})
    bad = init.replace(init.split("hash=")[1][:8], "deadbeef")
    with pytest.raises(TelegramAuthError):
        validate_webapp_init_data(bad, bot_token=token)


def test_expired_init_data():
    token = "123456:ABC-TEST-TOKEN"
    init = _make_init_data(token, {"id": 7}, auth_date=int(time.time()) - 100_000)
    with pytest.raises(TelegramAuthError, match="expired"):
        validate_webapp_init_data(init, bot_token=token, max_age_seconds=60)


def test_require_admin(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "100,200")
    from meme_tracker.config import get_settings

    get_settings.cache_clear()
    assert require_admin_telegram_id(100) == 100
    with pytest.raises(TelegramAuthError):
        require_admin_telegram_id(999)
    with pytest.raises(TelegramAuthError):
        require_admin_telegram_id(None)


def test_missing_bot_token_denied():
    with pytest.raises(TelegramAuthError):
        validate_webapp_init_data("auth_date=1&hash=abc", bot_token="")
