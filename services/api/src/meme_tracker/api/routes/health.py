"""Health, readiness, provider, scanner and database status endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from meme_tracker import ENGINE_VERSION, POLICY_VERSION, __version__
from meme_tracker.config import get_settings
from meme_tracker.db import is_db_available, storage_mode, verify_supabase
from meme_tracker.providers.registry import get_registry

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "engine_version": ENGINE_VERSION,
        "policy_version": POLICY_VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
        "storage": storage_mode(),
    }


@router.get("/ready")
async def ready():
    settings = get_settings()
    db = verify_supabase()
    checks = {
        "supabase_url_configured": bool(settings.supabase_url),
        "supabase_reachable": bool(db.get("reachable")),
        "supabase_schema_ready": bool(db.get("schema_ready")),
        "telegram_token_configured": bool(settings.telegram_bot_token),
        "solana_rpc_configured": bool(settings.solana_rpc_url),
        "ethereum_rpc_configured": bool(settings.ethereum_rpc_url or settings.alchemy_api_key),
        "base_rpc_configured": bool(settings.base_rpc_url or settings.alchemy_api_key),
    }
    from meme_tracker.providers.posture import provider_posture

    # Ready for durable mode only when schema is ready; API can still serve without it
    return {
        "ready": True,  # process is up
        "durable": checks["supabase_schema_ready"],
        "checks": checks,
        "storage": storage_mode(),
        "db": {
            "missing_tables": db.get("missing_tables") or [],
            "error": db.get("error"),
        },
        "provider_posture": provider_posture(),
        "note": "Chains without RPC keys report DEGRADED/BLOCKED at scan time",
    }


@router.get("/api/v1/db/status")
async def db_status():
    """Detailed Supabase connectivity and schema status."""
    status = verify_supabase(force=True)
    return {
        **status,
        "storage": storage_mode(),
        "migration_hint": (
            "Apply supabase/migrations/20251018000001_initial_schema.sql "
            "in the Supabase SQL editor or via supabase db push"
            if status.get("missing_tables")
            else None
        ),
    }


@router.get("/api/v1/providers/health")
async def providers_health():
    registry = get_registry()
    results = []
    for p in registry.all():
        try:
            resp = await p.ping()
            snap = p.health_snapshot()
            snap["ping_ok"] = resp.ok
            snap["ping_error"] = resp.error
            snap["latency_ms"] = resp.latency_ms
            results.append(snap)
        except Exception as e:
            results.append({"name": p.name, "health": "UNHEALTHY", "error": str(e)})
    return {"providers": results}


@router.get("/api/v1/scanner/status")
async def scanner_status():
    settings = get_settings()
    db = verify_supabase()
    return {
        "desired": {
            "continuous_scanning": "ON",
            "automatic_scam_monitoring": "ON",
            "solana": "ON",
            "ethereum": "ON",
            "base": "ON",
            "telegram_qualification_alerts": "ON",
            "telegram_risk_alerts": "ON",
            "live_trading": "OFF",
            "automatic_buying": "OFF",
            "wallet_signing": "OFF",
        },
        "actual": {
            "service": "STOPPED",
            "reason": "Worker process not connected in this process; start meme_tracker.worker",
            "chains": {
                "solana": "BLOCKED" if not settings.solana_rpc_url else "STOPPED",
                "ethereum": "BLOCKED"
                if not (settings.ethereum_rpc_url or settings.alchemy_api_key)
                else "STOPPED",
                "base": "BLOCKED"
                if not (settings.base_rpc_url or settings.alchemy_api_key)
                else "STOPPED",
            },
        },
        "health": "UNKNOWN",
        "storage": storage_mode(),
        "db_available": is_db_available(),
        "db_schema_ready": bool(db.get("schema_ready")),
        "note": "Desired settings are defaults. Actual state requires the worker service.",
    }


@router.get("/api/v1/chains")
async def list_chains():
    return {
        "chains": [
            {"id": "solana", "name": "Solana Mainnet", "enabled": True},
            {"id": "ethereum", "name": "Ethereum Mainnet", "enabled": True},
            {"id": "base", "name": "Base Mainnet", "enabled": True},
        ]
    }


@router.get("/api/v1/settings/public")
async def public_settings():
    """Safe, non-secret runtime posture. See settings_public.build_public_settings."""
    from meme_tracker.settings_public import build_public_settings

    return build_public_settings()
