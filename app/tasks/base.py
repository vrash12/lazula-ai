"""Task definitions: the contract between apps and the hub (API_SPEC.md §4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.providers.base import Effort, ModelRef

Quality = Literal["best", "best_value"]


@dataclass(frozen=True)
class HouseStyleData:
    rules: list[str] = field(default_factory=list)
    always_tags: list[str] = field(default_factory=list)
    avoid_words: list[str] = field(default_factory=list)


class PostCheckFailed(ValueError):
    """The answer is schema-valid but breaks a rule (house style, safety). Next model is tried."""


PostProcess = Callable[[Any, Any, HouseStyleData], Any]


def _no_postprocess(output: Any, _input: Any, _style: HouseStyleData) -> Any:
    return output


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    version: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    prompt: str  # path under app/prompts, e.g. "sermon.captions/v1.md"
    chains: dict[Quality, tuple[ModelRef, ...]]
    max_tokens: int
    timeout_s: float
    example: Callable[[], BaseModel]
    effort: Effort | None = None
    postprocess: PostProcess = _no_postprocess


def chain(*refs: str) -> tuple[ModelRef, ...]:
    return tuple(ModelRef.parse(r) for r in refs)
