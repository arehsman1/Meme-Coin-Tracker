"""FastAPI dependencies for auth."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from meme_tracker.auth import (
    AppRole,
    Principal,
    TelegramAuthError,
    require_admin_telegram_id,
    validate_webapp_init_data,
)
from meme_tracker.config import get_settings


def principal_from_webapp(
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
) -> Principal:
    """
    Validate Mini App initData header. Does not trust X-Telegram-User-Id alone.
    """
    if not x_telegram_init_data:
        # Anonymous read-only principal for public endpoints that allow it
        return Principal(roles=[AppRole.READ_ONLY.value], source="anonymous")

    try:
        verified = validate_webapp_init_data(x_telegram_init_data)
    except TelegramAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    tg_id = verified.get("telegram_id")
    roles = [AppRole.USER.value]
    settings = get_settings()
    if tg_id is not None and tg_id in settings.admin_telegram_ids:
        # Env allowlist maps to admin (owner is explicit first ID if desired)
        admin_list = list(settings.admin_telegram_ids)
        if admin_list and tg_id == admin_list[0]:
            roles = [AppRole.OWNER.value, AppRole.ADMIN.value, AppRole.USER.value]
        else:
            roles = [AppRole.ADMIN.value, AppRole.USER.value]

    return Principal(
        telegram_id=tg_id,
        roles=roles,
        source="webapp",
    )


def require_permission(permission: str):
    def _dep(
        x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
    ) -> Principal:
        principal = principal_from_webapp(x_telegram_init_data)
        if not principal.has_permission(permission):
            raise HTTPException(status_code=403, detail=f"Forbidden: {permission}")
        return principal

    return _dep


def require_env_admin(
    x_telegram_user_id: Optional[str] = Header(default=None, alias="X-Telegram-User-Id"),
    x_telegram_init_data: Optional[str] = Header(default=None, alias="X-Telegram-Init-Data"),
) -> Principal:
    """
    Privileged operations: prefer verified initData; fall back only if initData
    validates. Never accept user id header without matching verified identity.
    """
    tg_id: Optional[int] = None
    if x_telegram_init_data:
        try:
            verified = validate_webapp_init_data(x_telegram_init_data)
            tg_id = verified.get("telegram_id")
        except TelegramAuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
    elif x_telegram_user_id:
        # Header alone is insufficient for privilege
        raise HTTPException(
            status_code=401,
            detail="X-Telegram-User-Id alone is not trusted; send X-Telegram-Init-Data",
        )

    try:
        require_admin_telegram_id(tg_id)
    except TelegramAuthError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    settings = get_settings()
    admin_list = list(settings.admin_telegram_ids)
    roles = [AppRole.ADMIN.value]
    if admin_list and tg_id == admin_list[0]:
        roles = [AppRole.OWNER.value, AppRole.ADMIN.value]

    return Principal(telegram_id=tg_id, roles=roles, source="webapp_admin")
