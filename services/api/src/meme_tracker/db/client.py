"""Supabase client wrapper.

When SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are missing the client is
unavailable and repositories fall back to an in-memory store so the API
remains usable for local evaluation testing.

When credentials are present we verify connectivity and (optionally) schema.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from meme_tracker.config import get_settings

log = logging.getLogger(__name__)

_client: Any = None
_available: Optional[bool] = None
_last_verify: Optional[dict[str, Any]] = None

# Tables we expect after applying 20251018000001_initial_schema.sql
REQUIRED_TABLES = (
    "tokens",
    "qualification_evaluations",
    "qualification_check_results",
    "notification_outbox",
    "scanner_job_runs",
    "scanner_service_settings",
)


def is_db_available() -> bool:
    global _available
    if _available is not None:
        return _available
    settings = get_settings()
    _available = bool(settings.supabase_url and settings.supabase_service_role_key)
    if not _available:
        log.warning(
            "Supabase not configured — using in-memory store. "
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for persistence."
        )
    return _available


def get_supabase():
    """Return supabase client or None if not configured / import failed."""
    global _client
    if not is_db_available():
        return None
    if _client is not None:
        return _client
    settings = get_settings()
    try:
        from supabase import create_client

        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        log.info("Supabase client initialised")
        return _client
    except ImportError:
        log.error("supabase package not installed — pip install supabase")
        return None
    except Exception as e:
        log.error("Failed to create Supabase client: %s", e)
        return None


def reset_client() -> None:
    """Test helper."""
    global _client, _available, _last_verify
    _client = None
    _available = None
    _last_verify = None


def verify_supabase(force: bool = False) -> dict[str, Any]:
    """
    Live connectivity + schema probe.

    Returns a structured status dict; never raises to callers.
    Does not fabricate success — failed queries are reported honestly.
    """
    global _last_verify
    if _last_verify is not None and not force:
        return _last_verify

    result: dict[str, Any] = {
        "configured": is_db_available(),
        "client_created": False,
        "reachable": False,
        "schema_ready": False,
        "missing_tables": [],
        "tables_ok": [],
        "error": None,
        "storage_mode": "memory",
    }

    if not result["configured"]:
        result["error"] = "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set"
        _last_verify = result
        return result

    sb = get_supabase()
    if sb is None:
        result["error"] = "Could not create Supabase client (package missing or bad credentials)"
        _last_verify = result
        return result

    result["client_created"] = True

    missing: list[str] = []
    ok_tables: list[str] = []
    for table in REQUIRED_TABLES:
        try:
            # limit 1 — cheap existence/permission check
            sb.table(table).select("*").limit(1).execute()
            ok_tables.append(table)
        except Exception as e:
            msg = str(e)
            # PostgREST returns clear errors for missing relations
            missing.append(table)
            log.warning("Supabase table check failed for %s: %s", table, msg)

    result["tables_ok"] = ok_tables
    result["missing_tables"] = missing
    result["reachable"] = len(ok_tables) > 0 or len(missing) == 0
    # reachable even if schema incomplete if at least one query got a structured response
    if missing and not ok_tables:
        # all failed — might be network/auth
        result["reachable"] = False
        result["error"] = f"All table probes failed. First missing/failed: {missing[0]}"
    elif missing:
        result["reachable"] = True
        result["error"] = (
            f"Schema incomplete — apply supabase/migrations/20251018000001_initial_schema.sql. "
            f"Missing: {', '.join(missing)}"
        )
    else:
        result["reachable"] = True
        result["schema_ready"] = True
        result["storage_mode"] = "supabase"

    # Prefer supabase only when schema is ready; otherwise memory is safer
    if result["schema_ready"]:
        result["storage_mode"] = "supabase"
    else:
        result["storage_mode"] = "memory" if not result["configured"] else "supabase_degraded"

    _last_verify = result
    return result


def should_use_supabase() -> bool:
    """True only when client works and required tables exist."""
    if not is_db_available():
        return False
    status = verify_supabase()
    return bool(status.get("schema_ready"))
