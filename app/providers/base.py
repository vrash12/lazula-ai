"""The provider interface every adapter implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from app.core.errors import TokenUsage

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class ModelRef:
    """`provider:model`, e.g. `anthropic:claude-opus-5-5`."""

    provider: str
    model: str

    @classmethod
    def parse(cls, value: str) -> ModelRef:
        provider, sep, model = value.partition(":")
        if not sep or not provider or not model:
            raise ValueError(f"Model reference must be 'provider:model', got {value!r}")
        return cls(provider, model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    system: str
    user: str
    output_schema: type[BaseModel]
    max_tokens: int
    timeout_s: float
    effort: Effort | None = None


@dataclass
class ProviderResult:
    """Unvalidated structured data plus real token usage. The engine validates `data`."""

    data: dict[str, Any]
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    stop_reason: str | None = None


class Provider(Protocol):
    name: str

    async def run(self, request: ProviderRequest) -> ProviderResult:
        """Call the provider. Raise ProviderUnavailable, ProviderRejected or
        ProviderOutputInvalid (app.core.errors) instead of returning bad data."""
        ...
