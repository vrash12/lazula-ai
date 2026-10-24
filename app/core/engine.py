"""The fallback engine: run a task through its model chain (ARCHITECTURE.md §7).

Retryable failures (provider unavailable, invalid or rule-breaking output)
move to the next model. A 400-class rejection stops the chain. A refusal
moves on once; a second refusal ends the task as `refused`. The total task
deadline is respected: no new attempt starts without time to finish.

Circuit breakers and budgets arrive in M2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.core.errors import (
    EngineFailure,
    ErrorCode,
    ProviderOutputInvalid,
    ProviderRejected,
    ProviderUnavailable,
    TokenUsage,
)
from app.core.logging import get_logger
from app.prompts import render
from app.providers.base import ModelRef, ProviderRequest
from app.providers.fake import FAKE_MODELS
from app.providers.registry import ProviderRegistry
from app.tasks.base import HouseStyleData, PostCheckFailed, Quality, TaskDefinition

log = get_logger()

_MIN_SECONDS_FOR_ATTEMPT = 2.0


@dataclass
class Attempt:
    ref: ModelRef
    outcome: str  # ok | unavailable | rejected | refused | output_invalid
    usage: TokenUsage = field(default_factory=TokenUsage)
    reason: str | None = None


@dataclass
class EngineResult:
    output: BaseModel
    ref: ModelRef
    attempts: list[Attempt]

    @property
    def fallback_used(self) -> bool:
        return len(self.attempts) > 1


def resolve_chain(
    task: TaskDefinition, quality: Quality, registry: ProviderRegistry, *, enable_fake: bool
) -> list[ModelRef]:
    """The task's chain, keeping only providers that are configured, plus fake models
    when enabled (development, test, staging smoke)."""
    refs = [ref for ref in task.chains[quality] if registry.get(ref.provider) is not None]
    if enable_fake and registry.get("fake") is not None:
        refs += [ModelRef("fake", name) for name in FAKE_MODELS]
    return refs


async def run_task(
    task: TaskDefinition,
    task_input: BaseModel,
    *,
    quality: Quality,
    house_style: HouseStyleData,
    registry: ProviderRegistry,
    enable_fake: bool,
) -> EngineResult:
    chain = resolve_chain(task, quality, registry, enable_fake=enable_fake)
    if not chain:
        raise EngineFailure(ErrorCode.AI_UNAVAILABLE)

    prompt = render(task.prompt, house_style=house_style, **dict(task_input))
    deadline = time.monotonic() + task.timeout_s
    attempts: list[Attempt] = []
    refusals = 0

    for ref in chain:
        remaining = deadline - time.monotonic()
        if remaining < _MIN_SECONDS_FOR_ATTEMPT:
            break
        provider = registry.get(ref.provider)
        if provider is None:  # resolve_chain only returns configured providers
            continue
        request = ProviderRequest(
            model=ref.model,
            system=prompt.system,
            user=prompt.user,
            output_schema=task.output_model,
            max_tokens=task.max_tokens,
            timeout_s=remaining,
            effort=task.effort,
        )
        try:
            result = await provider.run(request)
        except ProviderUnavailable as exc:
            attempts.append(Attempt(ref, "unavailable", exc.usage, exc.reason))
            log.warning("provider_unavailable", task=task.name, model=str(ref), reason=exc.reason)
            continue
        except ProviderRejected as exc:
            if exc.refusal:
                refusals += 1
                attempts.append(Attempt(ref, "refused", exc.usage, exc.reason))
                log.warning("provider_refused", task=task.name, model=str(ref))
                if refusals >= 2:
                    raise EngineFailure(ErrorCode.REFUSED, attempts) from None
                continue
            attempts.append(Attempt(ref, "rejected", exc.usage, exc.reason))
            log.warning("provider_rejected", task=task.name, model=str(ref), reason=exc.reason)
            break  # same input would be rejected again
        except ProviderOutputInvalid as exc:
            attempts.append(Attempt(ref, "output_invalid", exc.usage, exc.reason))
            log.warning("output_invalid", task=task.name, model=str(ref), reason=exc.reason)
            continue

        try:
            output = task.output_model.model_validate(result.data)
            output = task.postprocess(output, task_input, house_style)
        except (ValidationError, PostCheckFailed) as exc:
            reason = "schema" if isinstance(exc, ValidationError) else str(exc)
            attempts.append(Attempt(ref, "output_invalid", result.usage, reason))
            log.warning("output_invalid", task=task.name, model=str(ref), reason=reason)
            continue

        attempts.append(Attempt(ref, "ok", result.usage))
        return EngineResult(output=output, ref=ref, attempts=attempts)

    raise EngineFailure(_failure_code(attempts, refusals), attempts)


def _failure_code(attempts: list[Attempt], refusals: int) -> ErrorCode:
    outcomes = {a.outcome for a in attempts}
    if refusals:
        return ErrorCode.REFUSED
    if not outcomes or "unavailable" in outcomes:
        return ErrorCode.AI_UNAVAILABLE  # includes running out of time before any attempt
    return (
        ErrorCode.OUTPUT_INVALID
    )  # every model answered, none validly (or the request was rejected)
