"""
Application roles and permission checks.

Roles: owner, admin, analyst, user, service_worker, read_only

Privilege elevation never happens from untrusted client input alone.
Owner/admin bootstrap uses TELEGRAM_ADMIN_IDS + verified Telegram identity.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional


class AppRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    USER = "user"
    SERVICE_WORKER = "service_worker"
    READ_ONLY = "read_only"


# Capability matrix (mirrors role_permissions seed)
PERMISSIONS: dict[AppRole, frozenset[str]] = {
    AppRole.OWNER: frozenset(
        {
            "settings.global.write",
            "roles.assign",
            "policies.write",
            "providers.write",
            "audit.read",
            "settings.scanner.write",
            "jobs.manage",
            "tokens.evaluate",
            "tokens.read",
            "evidence.read",
            "analytics.read",
            "watchlist.manage",
            "alerts.manage",
            "wallets.manage",
            "wallets.read",
        }
    ),
    AppRole.ADMIN: frozenset(
        {
            "settings.scanner.write",
            "providers.write",
            "jobs.manage",
            "audit.read",
            "tokens.evaluate",
            "tokens.read",
            "evidence.read",
            "analytics.read",
            "watchlist.manage",
            "alerts.manage",
            "wallets.manage",
            "wallets.read",
        }
    ),
    AppRole.ANALYST: frozenset(
        {
            "tokens.read",
            "evidence.read",
            "analytics.read",
            "wallets.read",
        }
    ),
    AppRole.USER: frozenset(
        {
            "tokens.read",
            "watchlist.manage",
            "alerts.manage",
            "wallets.read",
        }
    ),
    AppRole.SERVICE_WORKER: frozenset(
        {
            "jobs.claim",
            "tokens.write",
            "notifications.send",
            "heartbeats.write",
            "tokens.evaluate",
            "tokens.read",
            "wallets.manage",
            "wallets.read",
        }
    ),
    AppRole.READ_ONLY: frozenset(
        {
            "tokens.read",
            "analytics.read",
            "wallets.read",
        }
    ),
}


def roles_have_permission(roles: Iterable[str], permission: str) -> bool:
    for r in roles:
        try:
            role = AppRole(r)
        except ValueError:
            # legacy aliases
            if r == "operator":
                role = AppRole.ADMIN
            elif r == "viewer":
                role = AppRole.READ_ONLY
            else:
                continue
        if permission in PERMISSIONS.get(role, frozenset()):
            return True
    return False


def can_assign_role(actor_roles: Iterable[str], target_role: str) -> bool:
    """
    - Only owner may assign owner
    - owner/admin may assign admin/analyst/user/read_only
    - Nobody may assign service_worker via client APIs
    """
    actors = set(actor_roles)
    if target_role == AppRole.SERVICE_WORKER.value:
        return False
    if target_role == AppRole.OWNER.value:
        return AppRole.OWNER.value in actors
    if target_role in {
        AppRole.ADMIN.value,
        AppRole.ANALYST.value,
        AppRole.USER.value,
        AppRole.READ_ONLY.value,
    }:
        return bool(actors & {AppRole.OWNER.value, AppRole.ADMIN.value})
    return False


class Principal:
    """Authenticated actor for API authorization."""

    def __init__(
        self,
        *,
        telegram_id: Optional[int] = None,
        user_id: Optional[str] = None,
        roles: Optional[list[str]] = None,
        source: str = "unknown",
    ):
        self.telegram_id = telegram_id
        self.user_id = user_id
        self.roles = list(roles or [])
        self.source = source  # telegram_bot | webapp | service | env_admin

    def has_permission(self, permission: str) -> bool:
        return roles_have_permission(self.roles, permission)

    def require(self, permission: str) -> None:
        if not self.has_permission(permission):
            raise PermissionError(f"Missing permission: {permission}")

    @property
    def is_privileged(self) -> bool:
        return bool(set(self.roles) & {AppRole.OWNER.value, AppRole.ADMIN.value})
