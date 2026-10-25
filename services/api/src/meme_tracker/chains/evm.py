"""Ethereum and Base (EVM) evidence collectors.

Uses JSON-RPC (Alchemy or other) + DexScreener.
Bytecode and common function-selector heuristics for:
  - ownership / mint
  - pause / blacklist presence
  - proxy (EIP-1967 slot / DELEGATECALL density)

Does not claim source-code verification. Absence of a selector is not proof of safety.
"""

from __future__ import annotations

from typing import Optional

from meme_tracker.chains.base_adapter import (
    ChainAdapter,
    CollectedEvidence,
    make_check,
    now,
)
from meme_tracker.models.enums import Chain, CheckResult, SellabilityStatus
from meme_tracker.models.evidence import (
    EvidenceItem,
    LiquiditySnapshot,
    SellabilityEvidence,
    TokenIdentity,
)
from meme_tracker.providers.registry import ProviderRegistry
from meme_tracker.sellability.assessment import assess_from_dex_pairs

# ERC-20 / Ownable / common scam-related selectors (4-byte)
SELECTOR_NAME = "0x06fdde03"
SELECTOR_SYMBOL = "0x95d89b41"
SELECTOR_DECIMALS = "0x313ce567"
SELECTOR_TOTAL_SUPPLY = "0x18160ddd"
SELECTOR_OWNER = "0x8da5cb5b"  # owner()
SELECTOR_GET_OWNER = "0x893d20e8"  # getOwner()
SELECTOR_MINT = "0x40c10f19"  # mint(address,uint256)
SELECTOR_PAUSE = "0x8456cb59"  # pause()
SELECTOR_PAUSED = "0x5c975abb"  # paused()
SELECTOR_UNPAUSE = "0x3f4ba83a"  # unpause()
SELECTOR_BLACKLIST = "0xf9f92be4"  # blacklist(address) — varies
SELECTOR_IS_BLACKLISTED = "0xfe575a87"  # isBlacklisted(address) — common variant
SELECTOR_IS_BLACKLISTED2 = "0xe47d6060"  # isBlackListed(address)
SELECTOR_EXCLUDE_FROM_FEE = "0x437823ec"  # common tax token pattern

# EIP-1967 implementation slot
EIP1967_IMPL_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)
ZERO_ADDR = "0x" + ("0" * 40)
ZERO_WORD = "0x" + ("0" * 64)


def _hex_has_selector(code: str, selector: str) -> bool:
    """True if 4-byte selector appears in runtime bytecode (heuristic)."""
    if not code or code == "0x":
        return False
    sel = selector.lower().replace("0x", "")
    return sel in code.lower().replace("0x", "")


def _count_delegatecall(code: str) -> int:
    """Count DELEGATECALL opcodes (0xf4) — rough proxy signal."""
    if not code or code == "0x":
        return 0
    body = code.lower().replace("0x", "")
    return body.count("f4")


def _decode_address_word(word: Optional[str]) -> Optional[str]:
    if not word or not isinstance(word, str):
        return None
    w = word.lower()
    if not w.startswith("0x"):
        w = "0x" + w
    if len(w) < 42:
        return None
    # last 20 bytes
    addr = "0x" + w[-40:]
    return addr


class EvmAdapter(ChainAdapter):
    def __init__(self, registry: ProviderRegistry, chain: Chain):
        super().__init__(registry)
        self.chain = chain

    def _validate_address_format(self, address: str) -> bool:
        return (
            bool(address)
            and address.startswith("0x")
            and len(address) == 42
            and all(c in "0123456789abcdefABCDEF" for c in address[2:])
        )

    async def _eth_call(self, rpc, to: str, data: str):
        return await rpc.call("eth_call", [{"to": to, "data": data}, "latest"])

    async def collect(self, address: str) -> CollectedEvidence:
        limitations: list[str] = []
        evidence: list[EvidenceItem] = []
        checks = []
        liquidity: list[LiquiditySnapshot] = []
        address = address.lower() if address.startswith("0x") else address

        if not self._validate_address_format(address):
            identity = TokenIdentity(chain=self.chain, address=address)
            checks.append(
                make_check(
                    "identity_valid",
                    "Token identity",
                    CheckResult.FAIL,
                    "Address format invalid for EVM",
                    severity="critical",
                )
            )
            return CollectedEvidence(
                identity=identity, checks=checks, limitations=["invalid address"]
            )

        identity = TokenIdentity(chain=self.chain, address=address)
        rpc = self.registry.get_rpc(self.chain)
        code_hex = ""

        if rpc is None:
            checks.append(
                make_check(
                    "identity_valid",
                    "Token identity",
                    CheckResult.UNKNOWN,
                    f"{self.chain.value} RPC not registered",
                    severity="critical",
                )
            )
            limitations.append("no rpc")
        else:
            code_resp = await rpc.call("eth_getCode", [address, "latest"])
            code_hex = code_resp.data if code_resp.ok and code_resp.data else ""
            evidence.append(
                EvidenceItem(
                    key="bytecode",
                    value=(code_hex[:66] + "…") if code_hex and code_hex != "0x" else code_hex,
                    source=rpc.name,
                    observed_at=now(),
                    retrieval_status="ok" if code_resp.ok else "error",
                )
            )

            if not code_resp.ok:
                checks.append(
                    make_check(
                        "identity_valid",
                        "Token identity",
                        CheckResult.UNKNOWN,
                        f"RPC error: {code_resp.error}",
                        severity="critical",
                    )
                )
            elif not code_hex or code_hex == "0x":
                checks.append(
                    make_check(
                        "identity_valid",
                        "Token identity",
                        CheckResult.FAIL,
                        "No contract code at address",
                        severity="critical",
                    )
                )
            else:
                checks.append(
                    make_check(
                        "identity_valid",
                        "Token identity",
                        CheckResult.PASS,
                        "Contract code present",
                        severity="critical",
                        evidence_refs=["bytecode"],
                    )
                )

            # ERC-20 soft identity
            for sel, key in [
                (SELECTOR_DECIMALS, "decimals"),
                (SELECTOR_TOTAL_SUPPLY, "total_supply"),
                (SELECTOR_NAME, "name_call"),
                (SELECTOR_SYMBOL, "symbol_call"),
            ]:
                resp = await self._eth_call(rpc, address, sel)
                evidence.append(
                    EvidenceItem(
                        key=key,
                        value=resp.data if resp.ok else None,
                        source=rpc.name,
                        observed_at=now(),
                        retrieval_status="ok" if resp.ok else "error",
                    )
                )
                if key == "decimals" and resp.ok and resp.data and resp.data != "0x":
                    try:
                        identity.decimals = int(resp.data, 16)
                    except Exception:
                        pass

            # --- Ownership / mint control ---
            owner_addr = None
            owner_resp = await self._eth_call(rpc, address, SELECTOR_OWNER)
            evidence.append(
                EvidenceItem(
                    key="owner",
                    value=owner_resp.data if owner_resp.ok else None,
                    source=rpc.name,
                    observed_at=now(),
                    retrieval_status="ok" if owner_resp.ok else "error",
                    limitations=["owner() not universal"],
                )
            )
            if owner_resp.ok and owner_resp.data and owner_resp.data != "0x":
                owner_addr = _decode_address_word(owner_resp.data)

            has_mint_sel = _hex_has_selector(code_hex, SELECTOR_MINT)
            evidence.append(
                EvidenceItem(
                    key="selector_mint",
                    value=has_mint_sel,
                    source="bytecode_heuristic",
                    observed_at=now(),
                    retrieval_status="ok",
                    limitations=["selector presence ≠ callable by arbitrary caller"],
                )
            )

            if owner_addr == ZERO_ADDR and not has_mint_sel:
                checks.append(
                    make_check(
                        "mint_authority_renounced_or_safe",
                        "Mint / supply control",
                        CheckResult.PASS,
                        "owner() is zero and no mint(address,uint256) selector in bytecode",
                        severity="critical",
                        evidence_refs=["owner", "selector_mint"],
                    )
                )
            elif owner_addr == ZERO_ADDR and has_mint_sel:
                checks.append(
                    make_check(
                        "mint_authority_renounced_or_safe",
                        "Mint / supply control",
                        CheckResult.UNKNOWN,
                        "owner() is zero but mint selector present — verify access control",
                        severity="critical",
                        evidence_refs=["owner", "selector_mint"],
                    )
                )
            elif owner_addr and owner_addr != ZERO_ADDR and has_mint_sel:
                checks.append(
                    make_check(
                        "mint_authority_renounced_or_safe",
                        "Mint / supply control",
                        CheckResult.FAIL,
                        f"Active owner {owner_addr} and mint selector present",
                        severity="critical",
                        evidence_refs=["owner", "selector_mint"],
                    )
                )
            elif owner_addr and owner_addr != ZERO_ADDR:
                checks.append(
                    make_check(
                        "mint_authority_renounced_or_safe",
                        "Mint / supply control",
                        CheckResult.UNKNOWN,
                        f"Owner set ({owner_addr}); mint selector not detected — incomplete",
                        severity="critical",
                        evidence_refs=["owner"],
                    )
                )
            else:
                checks.append(
                    make_check(
                        "mint_authority_renounced_or_safe",
                        "Mint / supply control",
                        CheckResult.UNKNOWN,
                        "Could not resolve owner()/mint selectors",
                        severity="critical",
                    )
                )

            # --- Pause / blacklist ---
            has_pause = _hex_has_selector(code_hex, SELECTOR_PAUSE) or _hex_has_selector(
                code_hex, SELECTOR_PAUSED
            )
            has_blacklist = (
                _hex_has_selector(code_hex, SELECTOR_IS_BLACKLISTED)
                or _hex_has_selector(code_hex, SELECTOR_IS_BLACKLISTED2)
                or _hex_has_selector(code_hex, SELECTOR_BLACKLIST)
            )
            evidence.append(
                EvidenceItem(
                    key="selector_pause",
                    value=has_pause,
                    source="bytecode_heuristic",
                    observed_at=now(),
                    retrieval_status="ok",
                )
            )
            evidence.append(
                EvidenceItem(
                    key="selector_blacklist",
                    value=has_blacklist,
                    source="bytecode_heuristic",
                    observed_at=now(),
                    retrieval_status="ok",
                )
            )

            # paused() live call if selector exists
            paused_state = None
            if has_pause:
                paused_resp = await self._eth_call(rpc, address, SELECTOR_PAUSED)
                if paused_resp.ok and paused_resp.data and paused_resp.data != "0x":
                    try:
                        paused_state = int(paused_resp.data, 16) != 0
                    except Exception:
                        paused_state = None
                evidence.append(
                    EvidenceItem(
                        key="paused_state",
                        value=paused_state,
                        source=rpc.name,
                        observed_at=now(),
                        retrieval_status="ok" if paused_resp.ok else "error",
                    )
                )

            if has_blacklist:
                checks.append(
                    make_check(
                        "freeze_authority_safe",
                        "Freeze / blacklist ability",
                        CheckResult.FAIL,
                        "Blacklist-related selector(s) present in bytecode",
                        severity="critical",
                        evidence_refs=["selector_blacklist"],
                    )
                )
            else:
                checks.append(
                    make_check(
                        "freeze_authority_safe",
                        "Freeze / blacklist ability",
                        CheckResult.PASS,
                        "No common blacklist selectors detected in bytecode",
                        severity="critical",
                        evidence_refs=["selector_blacklist"],
                    )
                )

            if paused_state is True:
                checks.append(
                    make_check(
                        "no_blacklist_or_pause_trap",
                        "Pause / transfer restriction",
                        CheckResult.FAIL,
                        "Contract reports paused() == true",
                        severity="critical",
                        evidence_refs=["paused_state"],
                    )
                )
            elif has_pause:
                checks.append(
                    make_check(
                        "no_blacklist_or_pause_trap",
                        "Pause / transfer restriction",
                        CheckResult.UNKNOWN,
                        "Pause selectors present; contract not currently paused (or paused() unread)",
                        severity="critical",
                        evidence_refs=["selector_pause"],
                    )
                )
            else:
                checks.append(
                    make_check(
                        "no_blacklist_or_pause_trap",
                        "Pause / transfer restriction",
                        CheckResult.PASS,
                        "No common pause selectors detected in bytecode",
                        severity="critical",
                        evidence_refs=["selector_pause"],
                    )
                )

            # --- Proxy heuristic ---
            impl_slot = await rpc.call(
                "eth_getStorageAt", [address, EIP1967_IMPL_SLOT, "latest"]
            )
            impl_addr = None
            if impl_slot.ok and impl_slot.data and impl_slot.data not in (ZERO_WORD, "0x"):
                impl_addr = _decode_address_word(impl_slot.data)
                if impl_addr == ZERO_ADDR:
                    impl_addr = None
            delegate_count = _count_delegatecall(code_hex)
            evidence.append(
                EvidenceItem(
                    key="proxy_eip1967_impl",
                    value=impl_addr,
                    source=rpc.name,
                    observed_at=now(),
                    retrieval_status="ok" if impl_slot.ok else "error",
                    limitations=["EIP-1967 slot only; other proxy patterns possible"],
                )
            )
            evidence.append(
                EvidenceItem(
                    key="delegatecall_opcode_count",
                    value=delegate_count,
                    source="bytecode_heuristic",
                    observed_at=now(),
                    retrieval_status="ok",
                )
            )
            if impl_addr:
                limitations.append(
                    f"Upgradeable proxy detected (impl={impl_addr}) — logic can change"
                )
            elif delegate_count >= 3:
                limitations.append(
                    f"Elevated DELEGATECALL count ({delegate_count}) — possible proxy/custom routing"
                )

            limitations.append(
                "Bytecode selector heuristics can false-positive/negative; not a full audit"
            )

        # Liquidity via DexScreener
        dex = self.registry.get_dexscreener()
        if dex:
            pair_resp = await dex.get_token_pairs(self.chain, address)
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
                        f"{len(liquidity)} pair(s)" if liquidity else "No pairs",
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
                    "DexScreener unavailable",
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
            limitations=limitations,
        )


class EthereumAdapter(EvmAdapter):
    def __init__(self, registry: ProviderRegistry):
        super().__init__(registry, Chain.ETHEREUM)


class BaseAdapter(EvmAdapter):
    def __init__(self, registry: ProviderRegistry):
        super().__init__(registry, Chain.BASE)
