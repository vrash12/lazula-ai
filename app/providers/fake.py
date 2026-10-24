"""A deterministic fake provider for development, tests and staging smoke tests.

Never enabled in production (Settings rejects ENABLE_FAKE_PROVIDER there).
By default it returns each task's example output. Tests can script behaviors
per model, consumed in order: "ok", "unavailable", "refuse", "reject",
"invalid_json", or a dict to return as the answer.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.core.errors import ProviderOutputInvalid, ProviderRejected, ProviderUnavailable, TokenUsage
from app.providers.base import ProviderRequest, ProviderResult

Behavior = str | dict[str, Any]

FAKE_MODELS = ("primary", "secondary")


class FakeProvider:
    name = "fake"

    def __init__(self, example_for: Callable[[type[BaseModel]], BaseModel]) -> None:
        self._example_for = example_for
        self._scripts: dict[str, deque[Behavior]] = defaultdict(deque)
        self.calls: list[ProviderRequest] = []

    def script(self, model: str, *behaviors: Behavior) -> None:
        self._scripts[model].extend(behaviors)

    def reset(self) -> None:
        self._scripts.clear()
        self.calls.clear()

    async def run(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append(request)
        behavior: Behavior = (
            self._scripts[request.model].popleft() if self._scripts[request.model] else "ok"
        )
        usage = TokenUsage(input_tokens=1000, cached_input_tokens=800, output_tokens=200)
        if behavior == "unavailable":
            raise ProviderUnavailable("fake_unavailable")
        if behavior == "refuse":
            raise ProviderRejected("refusal", usage, refusal=True)
        if behavior == "reject":
            raise ProviderRejected("fake_bad_request")
        if behavior == "invalid_json":
            raise ProviderOutputInvalid("invalid_json", usage)
        if isinstance(behavior, dict):
            data = behavior
        else:
            data = self._example_for(request.output_schema).model_dump(mode="json")
        return ProviderResult(data=data, model=request.model, usage=usage, stop_reason="end_turn")
