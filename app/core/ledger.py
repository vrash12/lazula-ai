"""Usage and cost recording (INV-08, COST_AND_PERFORMANCE.md §3.1).

M1 writes ledger rows synchronously after the response is computed. The
Redis-stream outbox (ARCHITECTURE.md §16) replaces this in M2.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TokenUsage
from app.core.logging import get_logger
from app.models import AIModel, RequestRecord, UsageEntry

log = get_logger()

_PER_MILLION = Decimal(1_000_000)


def cost_of(usage: TokenUsage, price: AIModel | None) -> Decimal:
    if price is None:
        return Decimal(0)
    uncached = max(usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens, 0)
    total = (
        Decimal(uncached) * price.input_price
        + Decimal(usage.cached_input_tokens) * price.cached_input_price
        + Decimal(usage.cache_write_tokens) * price.cache_write_price
        + Decimal(usage.output_tokens) * price.output_price
    )
    return (total / _PER_MILLION).quantize(Decimal("0.00000001"))


async def record_attempts(
    session: AsyncSession,
    record: RequestRecord,
    attempts: list[tuple[str, str, str, TokenUsage]],
    *,
    start: int = 0,
) -> Decimal:
    """Write one ledger row per attempt: (provider, model, outcome, usage). Returns total cost.

    `start` is the number of ledger rows the request already has (a retry after a
    failure), so attempt numbers stay unique."""
    total = Decimal(0)
    price_cache: dict[tuple[str, str], AIModel | None] = {}
    for number, (provider, model, outcome, usage) in enumerate(attempts, start=start + 1):
        key = (provider, model)
        if key not in price_cache:
            price_cache[key] = await session.scalar(
                select(AIModel).where(AIModel.provider == provider, AIModel.model_id == model)
            )
            if price_cache[key] is None:
                log.warning("price_missing", provider=provider, model=model)
        cost = cost_of(usage, price_cache[key])
        total += cost
        session.add(
            UsageEntry(
                request_record_id=record.id,
                tenant_id=record.tenant_id,
                attempt=number,
                provider=provider,
                model=model,
                outcome=outcome,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                output_tokens=usage.output_tokens,
                cost=cost,
            )
        )
    return total
