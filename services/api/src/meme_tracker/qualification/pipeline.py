"""End-to-end evaluate pipeline: chain collect → engine → decision."""

from __future__ import annotations

from meme_tracker.chains import get_adapter
from meme_tracker.models.enums import Chain
from meme_tracker.models.evidence import QualificationDecision
from meme_tracker.providers.registry import get_registry
from meme_tracker.qualification.engine import QualificationEngine, create_engine


async def evaluate_token(
    chain: Chain | str,
    address: str,
    *,
    engine: QualificationEngine | None = None,
) -> QualificationDecision:
    """
    Collect evidence for a token and run the authoritative qualification engine.
    Does not persist — caller is responsible for DB write.
    """
    if isinstance(chain, str):
        chain = Chain(chain.lower())

    registry = get_registry()
    adapter = get_adapter(chain, registry)
    collected = await adapter.collect(address)

    eng = engine or create_engine()
    decision = eng.evaluate(
        identity=collected.identity,
        evidence=collected.evidence,
        checks=collected.checks,
        sellability=collected.sellability,
        liquidity=collected.liquidity,
        holders=collected.holders,
    )
    return decision
