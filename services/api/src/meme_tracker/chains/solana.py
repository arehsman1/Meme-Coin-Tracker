"""Solana evidence collector.

Uses public RPC / Helius when configured, and DexScreener for pairs.
Inspects classic SPL mint authorities and Token-2022 extension signals
when jsonParsed data is available.

Missing credentials produce UNKNOWN checks, not fabricated safety.
"""

from __future__ import annotations

from typing import Any, Optional

from meme_tracker.chains.base_adapter import (
    ChainAdapter,
    CollectedEvidence,
    make_check,
    now,
)
from meme_tracker.models.enums import Chain, CheckResult, SellabilityStatus
from meme_tracker.models.evidence import (
    EvidenceItem,
    HolderSnapshot,
    LiquiditySnapshot,
    SellabilityEvidence,
    TokenIdentity,
)
from meme_tracker.providers.registry import ProviderRegistry
from meme_tracker.sellability.assessment import assess_from_dex_pairs

# Official program IDs
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Token-2022 extensions that elevate transfer / freeze risk when present
RISKY_EXTENSIONS = {
    "transferHook",
    "permanentDelegate",
    "defaultAccountState",
    "nonTransferable",
    "transferFeeConfig",
    "mintCloseAuthority",
    "pausable",  # if present in future layouts
}


class SolanaAdapter(ChainAdapter):
    chain = Chain.SOLANA

    def _validate_address_format(self, address: str) -> bool:
        return bool(address) and 32 <= len(address) <= 48 and not address.startswith("0x")

    def _parse_mint_info(self, account_data: Any) -> tuple[Optional[dict], Optional[str]]:
        """Return (info dict, owner_program) from getAccountInfo jsonParsed result."""
        if not isinstance(account_data, dict):
            return None, None
        value = account_data.get("value")
        if not isinstance(value, dict):
            return None, None
        owner = value.get("owner")
        data = value.get("data")
        if isinstance(data, dict):
            parsed = data.get("parsed")
            if isinstance(parsed, dict):
                info = parsed.get("info")
                if isinstance(info, dict):
                    return info, owner
        return None, owner

    def _extension_names(self, info: dict) -> list[str]:
        """Extract Token-2022 extension type names from parsed mint info."""
        names: list[str] = []
        extensions = info.get("extensions")
        if not isinstance(extensions, list):
            return names
        for ext in extensions:
            if not isinstance(ext, dict):
                continue
            # shapes: {"extension": "transferFeeConfig", "state": {...}} or nested
            name = ext.get("extension") or ext.get("extensionType")
            if isinstance(name, str):
                names.append(name)
            state = ext.get("state")
            if isinstance(state, dict):
                for k in state.keys():
                    if k not in names:
                        names.append(k)
        return names

    async def collect(self, address: str) -> CollectedEvidence:
        limitations: list[str] = []
        evidence: list[EvidenceItem] = []
        checks = []
        liquidity: list[LiquiditySnapshot] = []
        holders: Optional[HolderSnapshot] = None
        sellability = None

        if not self._validate_address_format(address):
            identity = TokenIdentity(chain=self.chain, address=address)
            checks.append(
                make_check(
                    "identity_valid",
                    "Token identity",
                    CheckResult.FAIL,
                    "Address format invalid for Solana",
                    severity="critical",
                )
            )
            return CollectedEvidence(
                identity=identity,
                checks=checks,
                evidence=evidence,
                limitations=["invalid address format"],
            )

        identity = TokenIdentity(chain=self.chain, address=address)
        rpc = self.registry.get_rpc(Chain.SOLANA)

        if rpc is None:
            checks.append(
                make_check(
                    "identity_valid",
                    "Token identity",
                    CheckResult.UNKNOWN,
                    "Solana RPC provider not registered",
                    severity="critical",
                )
            )
            limitations.append("no solana rpc")
        else:
            resp = await rpc.call(
                "getAccountInfo",
                [address, {"encoding": "jsonParsed"}],
            )
            evidence.append(
                EvidenceItem(
                    key="account_info",
                    value=resp.data if resp.ok else None,
                    source=rpc.name,
                    observed_at=now(),
                    retrieval_status="ok" if resp.ok else "error",
                    limitations=resp.limitations,
                )
            )

            if not resp.ok:
                checks.append(
                    make_check(
                        "identity_valid",
                        "Token identity",
                        CheckResult.UNKNOWN,
                        f"RPC error: {resp.error}",
                        severity="critical",
                    )
                )
                limitations.append(f"rpc:{resp.error}")
            elif resp.data is None or (
                isinstance(resp.data, dict) and resp.data.get("value") is None
            ):
                checks.append(
                    make_check(
                        "identity_valid",
                        "Token identity",
                        CheckResult.FAIL,
                        "Account not found on Solana",
                        severity="critical",
                    )
                )
            else:
                info, owner_program = self._parse_mint_info(resp.data)
                is_token_2022 = owner_program == TOKEN_2022_PROGRAM
                is_classic_spl = owner_program == TOKEN_PROGRAM

                evidence.append(
                    EvidenceItem(
                        key="token_program",
                        value=owner_program,
                        source=rpc.name,
                        observed_at=now(),
                        retrieval_status="ok",
                        limitations=[]
                        if (is_token_2022 or is_classic_spl)
                        else ["owner is not a known SPL token program"],
                    )
                )

                if not info:
                    checks.append(
                        make_check(
                            "identity_valid",
                            "Token identity",
                            CheckResult.UNKNOWN
                            if owner_program
                            else CheckResult.FAIL,
                            "Could not parse mint account as SPL token",
                            severity="critical",
                        )
                    )
                    for cid, name in [
                        ("mint_authority_renounced_or_safe", "Mint authority"),
                        ("freeze_authority_safe", "Freeze authority"),
                        ("no_blacklist_or_pause_trap", "Blacklist / pause / transfer hook"),
                    ]:
                        checks.append(
                            make_check(
                                cid,
                                name,
                                CheckResult.UNKNOWN,
                                "Mint account not parseable",
                                severity="critical",
                            )
                        )
                    limitations.append("unparsed mint account")
                else:
                    checks.append(
                        make_check(
                            "identity_valid",
                            "Token identity",
                            CheckResult.PASS,
                            f"Mint account OK ({'Token-2022' if is_token_2022 else 'SPL Token'})",
                            severity="critical",
                            evidence_refs=["account_info", "token_program"],
                        )
                    )

                    mint_auth = info.get("mintAuthority")
                    freeze_auth = info.get("freezeAuthority")
                    decimals = info.get("decimals")
                    supply = info.get("supply")
                    if decimals is not None:
                        try:
                            identity.decimals = int(decimals)
                        except (TypeError, ValueError):
                            pass

                    evidence.append(
                        EvidenceItem(
                            key="mint_authority",
                            value=mint_auth,
                            source=rpc.name,
                            observed_at=now(),
                            retrieval_status="ok",
                        )
                    )
                    evidence.append(
                        EvidenceItem(
                            key="freeze_authority",
                            value=freeze_auth,
                            source=rpc.name,
                            observed_at=now(),
                            retrieval_status="ok",
                        )
                    )
                    evidence.append(
                        EvidenceItem(
                            key="supply",
                            value=supply,
                            source=rpc.name,
                            observed_at=now(),
                            retrieval_status="ok",
                        )
                    )

                    if mint_auth is None:
                        checks.append(
                            make_check(
                                "mint_authority_renounced_or_safe",
                                "Mint authority",
                                CheckResult.PASS,
                                "Mint authority is null (renounced)",
                                severity="critical",
                                evidence_refs=["mint_authority"],
                            )
                        )
                    else:
                        checks.append(
                            make_check(
                                "mint_authority_renounced_or_safe",
                                "Mint authority",
                                CheckResult.FAIL,
                                f"Mint authority still set: {mint_auth}",
                                severity="critical",
                                evidence_refs=["mint_authority"],
                            )
                        )

                    if freeze_auth is None:
                        checks.append(
                            make_check(
                                "freeze_authority_safe",
                                "Freeze authority",
                                CheckResult.PASS,
                                "Freeze authority is null",
                                severity="critical",
                                evidence_refs=["freeze_authority"],
                            )
                        )
                    else:
                        checks.append(
                            make_check(
                                "freeze_authority_safe",
                                "Freeze authority",
                                CheckResult.FAIL,
                                f"Freeze authority still set: {freeze_auth}",
                                severity="critical",
                                evidence_refs=["freeze_authority"],
                            )
                        )

                    # Token-2022 extension analysis
                    ext_names = self._extension_names(info)
                    evidence.append(
                        EvidenceItem(
                            key="token2022_extensions",
                            value=ext_names,
                            source=rpc.name,
                            observed_at=now(),
                            retrieval_status="ok",
                            limitations=[]
                            if is_token_2022
                            else ["classic SPL — no Token-2022 extensions expected"],
                        )
                    )

                    risky = [e for e in ext_names if e in RISKY_EXTENSIONS]
                    if is_token_2022 and risky:
                        checks.append(
                            make_check(
                                "no_blacklist_or_pause_trap",
                                "Blacklist / pause / transfer hook",
                                CheckResult.FAIL,
                                f"Risky Token-2022 extension(s): {', '.join(risky)}",
                                severity="critical",
                                evidence_refs=["token2022_extensions"],
                            )
                        )
                    elif is_token_2022 and not ext_names:
                        checks.append(
                            make_check(
                                "no_blacklist_or_pause_trap",
                                "Blacklist / pause / transfer hook",
                                CheckResult.PASS,
                                "Token-2022 mint with no listed risky extensions in parsed data",
                                severity="critical",
                                evidence_refs=["token2022_extensions"],
                            )
                        )
                    elif is_classic_spl:
                        checks.append(
                            make_check(
                                "no_blacklist_or_pause_trap",
                                "Blacklist / pause / transfer hook",
                                CheckResult.PASS,
                                "Classic SPL Token program (no Token-2022 transfer hooks)",
                                severity="critical",
                                evidence_refs=["token_program"],
                            )
                        )
                    else:
                        checks.append(
                            make_check(
                                "no_blacklist_or_pause_trap",
                                "Blacklist / pause / transfer hook",
                                CheckResult.UNKNOWN,
                                "Token program / extensions not fully classified",
                                severity="critical",
                            )
                        )

            # Holder concentration via largest accounts
            try:
                largest = await rpc.call("getTokenLargestAccounts", [address])
                evidence.append(
                    EvidenceItem(
                        key="largest_accounts",
                        value=largest.data if largest.ok else None,
                        source=rpc.name,
                        observed_at=now(),
                        retrieval_status="ok" if largest.ok else "error",
                    )
                )
                if largest.ok and isinstance(largest.data, dict):
                    values = largest.data.get("value") or []
                    if isinstance(values, list) and values:
                        # amounts are strings; share of top holder among top accounts only
                        amounts = []
                        for v in values[:10]:
                            if isinstance(v, dict) and v.get("uiAmount") is not None:
                                try:
                                    amounts.append(float(v["uiAmount"]))
                                except (TypeError, ValueError):
                                    pass
                        total = sum(amounts) or 1.0
                        top = amounts[0] if amounts else 0.0
                        holders = HolderSnapshot(
                            top_holder_pct=round(100.0 * top / total, 2) if amounts else None,
                            top10_pct=round(100.0 * sum(amounts) / total, 2) if amounts else None,
                            unique_holders=None,
                            observed_at=now(),
                            source=rpc.name,
                        )
                        limitations.append(
                            "Holder % among top accounts only, not full circulating supply"
                        )
            except Exception as e:
                limitations.append(f"largest_accounts:{e}")

        # Liquidity via DexScreener
        dex = self.registry.get_dexscreener()
        if dex:
            pair_resp = await dex.get_token_pairs(Chain.SOLANA, address)
            evidence.append(
                EvidenceItem(
                    key="dex_pairs",
                    value=pair_resp.data if pair_resp.ok else None,
                    source=dex.name,
                    observed_at=now(),
                    retrieval_status="ok" if pair_resp.ok else "error",
                    limitations=pair_resp.limitations,
                )
            )
            if pair_resp.ok and pair_resp.data:
                pairs = pair_resp.data if isinstance(pair_resp.data, list) else [pair_resp.data]
                for p in pairs[:5]:
                    if not isinstance(p, dict):
                        continue
                    liq = p.get("liquidity") or {}
                    usd = liq.get("usd") if isinstance(liq, dict) else None
                    liquidity.append(
                        LiquiditySnapshot(
                            pool_address=p.get("pairAddress"),
                            dex=p.get("dexId"),
                            liquidity_usd=float(usd) if usd is not None else None,
                            quote_symbol=(p.get("quoteToken") or {}).get("symbol"),
                            observed_at=now(),
                            source=dex.name,
                        )
                    )
                    if not identity.name:
                        base = p.get("baseToken") or {}
                        identity.name = base.get("name")
                        identity.symbol = base.get("symbol")
                        identity.metadata_source = dex.name
                checks.append(
                    make_check(
                        "liquidity_present",
                        "Liquidity",
                        CheckResult.PASS if liquidity else CheckResult.UNKNOWN,
                        f"{len(liquidity)} pair(s) found" if liquidity else "Pairs empty",
                        severity="critical",
                        evidence_refs=["dex_pairs"],
                    )
                )
            else:
                checks.append(
                    make_check(
                        "liquidity_present",
                        "Liquidity",
                        CheckResult.UNKNOWN,
                        pair_resp.error or "No pair data",
                        severity="critical",
                    )
                )
        else:
            checks.append(
                make_check(
                    "liquidity_present",
                    "Liquidity",
                    CheckResult.UNKNOWN,
                    "DexScreener not available",
                    severity="critical",
                )
            )

        # Sellability from DexScreener market evidence (not a live swap)
        pair_payload = None
        for item in evidence:
            if item.key == "dex_pairs" and item.retrieval_status == "ok":
                pair_payload = item.value
                break
        sellability, sell_result, sell_reason = assess_from_dex_pairs(
            pair_payload, source="dexscreener"
        )
        checks.append(
            make_check(
                "sellability_not_failed",
                "Sellability",
                sell_result,
                sell_reason,
                severity="critical",
                evidence_refs=["dex_pairs"] if pair_payload else [],
            )
        )
        if sellability.status == SellabilityStatus.UNKNOWN:
            limitations.append("sellability incomplete")
        elif sellability.status == SellabilityStatus.LIMITED:
            limitations.append("sellability limited by market depth")

        return CollectedEvidence(
            identity=identity,
            checks=checks,
            evidence=evidence,
            sellability=sellability,
            liquidity=liquidity,
            holders=holders,
            limitations=limitations,
        )
