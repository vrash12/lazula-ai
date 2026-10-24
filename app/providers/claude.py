"""Anthropic (Claude) adapter.

Checked against Anthropic's Python SDK 2.3 and current API guidance
(October 2026):
- structured output through `output_config={"format": {"type": "json_schema", ...}}`
- depth and cost through `output_config={"effort": ...}`; no sampling parameters
- stable system prompt marked for prompt caching
- `stop_reason == "refusal"` and `"max_tokens"` are not successes
"""

from __future__ import annotations

import json

import anthropic
import httpx2
from anthropic.types import MessageParam, OutputConfigParam, TextBlockParam

from app.core.errors import (
    ProviderOutputInvalid,
    ProviderRejected,
    ProviderUnavailable,
    TokenUsage,
)
from app.providers.base import ProviderRequest, ProviderResult
from app.providers.schema import strict_json_schema

_RETRYABLE_STATUS = {408, 409, 429}


class ClaudeProvider:
    name = "anthropic"

    def __init__(
        self, api_key: str, *, max_retries: int = 1, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        # Low SDK retries: the hub's fallback engine owns resilience (AD-012).
        # `http_client` lets tests inject a mock transport so no real request is ever sent.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, max_retries=max_retries, http_client=http_client
        )

    async def run(self, request: ProviderRequest) -> ProviderResult:
        output_config: OutputConfigParam = {
            "format": {"type": "json_schema", "schema": strict_json_schema(request.output_schema)}
        }
        if request.effort:
            output_config["effort"] = request.effort
        system: list[TextBlockParam] = [
            {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[MessageParam] = [{"role": "user", "content": request.user}]
        try:
            response = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
                timeout=request.timeout_s,
            )
        except (
            anthropic.RateLimitError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
        ) as exc:
            raise ProviderUnavailable(type(exc).__name__) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in _RETRYABLE_STATUS:
                raise ProviderUnavailable(f"http_{exc.status_code}") from exc
            raise ProviderRejected(f"http_{exc.status_code}") from exc

        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            cached_input_tokens=response.usage.cache_read_input_tokens or 0,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
            output_tokens=response.usage.output_tokens,
        )
        if response.stop_reason == "refusal":
            raise ProviderRejected("refusal", usage, refusal=True)
        if response.stop_reason == "max_tokens":
            raise ProviderOutputInvalid("max_tokens", usage)

        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise ProviderOutputInvalid("no_text_block", usage)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderOutputInvalid("invalid_json", usage) from exc
        if not isinstance(data, dict):
            raise ProviderOutputInvalid("not_an_object", usage)
        return ProviderResult(
            data=data, model=request.model, usage=usage, stop_reason=response.stop_reason
        )
