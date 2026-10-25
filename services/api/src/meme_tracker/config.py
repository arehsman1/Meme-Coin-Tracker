"""Application configuration loaded from environment.

Works with pydantic-settings when available; falls back to os.environ.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.supabase_url: str = _env("SUPABASE_URL")
        self.supabase_service_role_key: str = _env("SUPABASE_SERVICE_ROLE_KEY")
        self.supabase_anon_key: str = _env("SUPABASE_ANON_KEY")

        self.telegram_bot_token: str = _env("TELEGRAM_BOT_TOKEN")
        self.telegram_admin_ids: str = _env("TELEGRAM_ADMIN_IDS")
        self.mini_app_url: str = _env("MINI_APP_URL")
        self.app_base_url: str = _env("APP_BASE_URL")

        self.solana_rpc_url: str = _env(
            "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
        )
        self.helius_api_key: Optional[str] = _env("HELIUS_API_KEY") or None
        self.ethereum_rpc_url: str = _env("ETHEREUM_RPC_URL")
        self.alchemy_api_key: Optional[str] = _env("ALCHEMY_API_KEY") or None
        self.base_rpc_url: str = _env("BASE_RPC_URL")
        self.dexscreener_enabled: bool = _env_bool("DEXSCREENER_ENABLED", True)

        self.log_level: str = _env("LOG_LEVEL", "INFO")
        self.scan_interval_seconds: int = int(_env("SCAN_INTERVAL_SECONDS", "45"))
        self.environment: str = _env("ENVIRONMENT", "development")
        self.api_host: str = _env("API_HOST", "0.0.0.0")
        self.api_port: int = int(_env("API_PORT", "8000"))

    @property
    def admin_telegram_ids(self) -> set[int]:
        if not self.telegram_admin_ids:
            return set()
        return {int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()}

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_telegram_ids


# Prefer pydantic-settings if installed
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class PydanticSettings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env", env_file_encoding="utf-8", extra="ignore"
        )

        supabase_url: str = ""
        supabase_service_role_key: str = ""
        supabase_anon_key: str = ""
        telegram_bot_token: str = ""
        telegram_admin_ids: str = ""
        mini_app_url: str = ""
        app_base_url: str = ""
        solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
        helius_api_key: Optional[str] = None
        ethereum_rpc_url: str = ""
        alchemy_api_key: Optional[str] = None
        base_rpc_url: str = ""
        dexscreener_enabled: bool = True
        log_level: str = "INFO"
        scan_interval_seconds: int = 45
        environment: str = "development"
        api_host: str = "0.0.0.0"
        api_port: int = 8000

        @property
        def admin_telegram_ids(self) -> set[int]:
            if not self.telegram_admin_ids:
                return set()
            return {
                int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()
            }

        def is_admin(self, telegram_id: int) -> bool:
            return telegram_id in self.admin_telegram_ids

    _USE_PYDANTIC = True
except ImportError:
    _USE_PYDANTIC = False


@lru_cache
def get_settings():
    if _USE_PYDANTIC:
        return PydanticSettings()
    return Settings()
