from meme_tracker.chains.base_adapter import ChainAdapter, CollectedEvidence
from meme_tracker.chains.solana import SolanaAdapter
from meme_tracker.chains.evm import EthereumAdapter, BaseAdapter
from meme_tracker.models.enums import Chain
from meme_tracker.providers.registry import ProviderRegistry, get_registry


def get_adapter(chain: Chain, registry: ProviderRegistry | None = None) -> ChainAdapter:
    reg = registry or get_registry()
    if chain == Chain.SOLANA:
        return SolanaAdapter(reg)
    if chain == Chain.ETHEREUM:
        return EthereumAdapter(reg)
    if chain == Chain.BASE:
        return BaseAdapter(reg)
    raise ValueError(f"Unsupported chain: {chain}")


__all__ = [
    "ChainAdapter",
    "CollectedEvidence",
    "SolanaAdapter",
    "EthereumAdapter",
    "BaseAdapter",
    "get_adapter",
]
