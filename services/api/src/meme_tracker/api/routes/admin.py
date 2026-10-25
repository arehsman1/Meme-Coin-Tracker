"""Administrator endpoints — require verified Telegram admin identity."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from meme_tracker.api.deps import require_env_admin
from meme_tracker.auth import Principal
from meme_tracker.db import storage_mode, verify_supabase
from meme_tracker.db.client import get_supabase, should_use_supabase

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class ScannerDesiredUpdate(BaseModel):
    continuous_scanning: Optional[str] = Field(None, pattern="^(ON|OFF)$")
    automatic_scam_monitoring: Optional[str] = Field(None, pattern="^(ON|OFF)$")
    telegram_qualification_alerts: Optional[str] = Field(None, pattern="^(ON|OFF)$")
    telegram_risk_alerts: Optional[str] = Field(None, pattern="^(ON|OFF)$")
    # Trading fields rejected if set to ON
    live_trading: Optional[str] = Field(None, pattern="^(ON|OFF)$")
    automatic_buying: Optional[str] = Field(None, pattern="^(ON|OFF)$")
    automatic_selling: Optional[str] = Field(None, pattern="^(ON|OFF)$")
    wallet_signing: Optional[str] = Field(None, pattern="^(ON|OFF)$")


class ChainDesiredUpdate(BaseModel):
    chain: str
    desired_state: str = Field(..., pattern="^(ON|OFF)$")


def _audit(actor: Principal, action: str, details: dict[str, Any]) -> None:
    if not should_use_supabase():
        return
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("audit_logs").insert(
            {
                "action": action,
                "resource_type": "scanner_settings",
                "details": {
                    **details,
                    "telegram_id": actor.telegram_id,
                    "roles": actor.roles,
                },
            }
        ).execute()
    except Exception:
        pass


@router.get("/me")
async def admin_me(principal: Principal = Depends(require_env_admin)):
    return {
        "telegram_id": principal.telegram_id,
        "roles": principal.roles,
        "source": principal.source,
        "storage": storage_mode(),
    }


@router.get("/scanner/settings")
async def get_scanner_settings(principal: Principal = Depends(require_env_admin)):
    principal.require("settings.scanner.write")  # same gate for read of admin view
    if not should_use_supabase():
        return {
            "storage": storage_mode(),
            "db": verify_supabase(),
            "settings": None,
            "note": "Supabase schema not ready — defaults apply in-process only",
        }
    sb = get_supabase()
    assert sb is not None
    svc = sb.table("scanner_service_settings").select("*").eq("id", 1).limit(1).execute()
    chains = sb.table("scanner_chain_settings").select("*").execute()
    return {
        "storage": "supabase",
        "service": (svc.data or [None])[0],
        "chains": chains.data or [],
    }


@router.patch("/scanner/settings")
async def patch_scanner_settings(
    body: ScannerDesiredUpdate,
    principal: Principal = Depends(require_env_admin),
):
    if not principal.has_permission("settings.scanner.write"):
        raise HTTPException(403, "Forbidden")

    data = body.model_dump(exclude_none=True)
    # Phase 11 hard gates — single source of truth
    from meme_tracker.trading.gates import reject_on_flags

    try:
        data = reject_on_flags(data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    if not data:
        raise HTTPException(400, "No fields to update")

    if not should_use_supabase():
        _audit(principal, "scanner.settings.patch_memory", data)
        return {
            "ok": True,
            "storage": storage_mode(),
            "applied": data,
            "note": "Persisted only in audit intent — apply Supabase migration for durability",
        }

    sb = get_supabase()
    assert sb is not None
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    sb.table("scanner_service_settings").update(data).eq("id", 1).execute()
    _audit(principal, "scanner.settings.patch", data)
    return {"ok": True, "storage": "supabase", "applied": data}


@router.patch("/scanner/chains")
async def patch_chain_settings(
    body: ChainDesiredUpdate,
    principal: Principal = Depends(require_env_admin),
):
    if not principal.has_permission("settings.scanner.write"):
        raise HTTPException(403, "Forbidden")
    chain = body.chain.lower()
    if chain not in ("solana", "ethereum", "base"):
        raise HTTPException(400, "Unsupported chain")

    if not should_use_supabase():
        _audit(
            principal,
            "scanner.chain.patch_memory",
            {"chain": chain, "desired_state": body.desired_state},
        )
        return {
            "ok": True,
            "storage": storage_mode(),
            "chain": chain,
            "desired_state": body.desired_state,
        }

    sb = get_supabase()
    assert sb is not None
    sb.table("scanner_chain_settings").update(
        {
            "desired_state": body.desired_state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("chain", chain).execute()
    _audit(
        principal,
        "scanner.chain.patch",
        {"chain": chain, "desired_state": body.desired_state},
    )
    return {"ok": True, "storage": "supabase", "chain": chain, "desired_state": body.desired_state}
