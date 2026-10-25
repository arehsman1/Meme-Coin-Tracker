"""Role and permission matrix tests."""

from meme_tracker.auth.roles import (
    AppRole,
    Principal,
    can_assign_role,
    roles_have_permission,
)


def test_owner_has_global_settings():
    assert roles_have_permission(["owner"], "settings.global.write")
    assert roles_have_permission(["owner"], "roles.assign")


def test_admin_cannot_global_owner_only():
    assert not roles_have_permission(["admin"], "settings.global.write")
    assert roles_have_permission(["admin"], "settings.scanner.write")


def test_user_watchlist_only():
    assert roles_have_permission(["user"], "watchlist.manage")
    assert not roles_have_permission(["user"], "jobs.manage")


def test_service_worker_no_client_elevation():
    assert roles_have_permission(["service_worker"], "jobs.claim")
    assert not can_assign_role(["owner"], "service_worker")
    assert not can_assign_role(["admin"], "service_worker")


def test_only_owner_assigns_owner():
    assert can_assign_role(["owner"], "owner")
    assert not can_assign_role(["admin"], "owner")
    assert can_assign_role(["admin"], "user")


def test_principal_require():
    p = Principal(telegram_id=1, roles=["user"])
    try:
        p.require("jobs.manage")
        assert False, "should raise"
    except PermissionError:
        pass
    p2 = Principal(telegram_id=1, roles=["admin"])
    p2.require("jobs.manage")


def test_legacy_aliases():
    assert roles_have_permission(["operator"], "settings.scanner.write")
    assert roles_have_permission(["viewer"], "tokens.read")
