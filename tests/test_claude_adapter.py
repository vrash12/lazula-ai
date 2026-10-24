"""Contract tests for the Claude adapter against recorded-shape API responses.

A mock HTTP transport is injected into the SDK, so no request ever leaves the
machine (INV-12). conftest.py also points ANTHROPIC_BASE_URL at an unreachable
local address as a second safety net.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx2 as httpx  # the Anthropic SDK 2.x uses httpx2, a fork of httpx
import pytest

from app.core.errors import ProviderOutputInvalid, ProviderRejected, ProviderUnavailable
from app.providers.base import ProviderRequest
from app.providers.claude import ClaudeProvider
from app.tasks.sermon_captions import CaptionsOutput

OUTPUT = {"options": [{"title": "t", "caption": "c", "hashtags": ["#a"]}]}

Handler = Callable[[httpx.Request], httpx.Response]


def _message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 300,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 0,
        },
    }


def _request() -> ProviderRequest:
    return ProviderRequest(
        model="claude-opus-5-5",
        system="stable instructions",
        user="<idea>data</idea>",
        output_schema=CaptionsOutput,
        max_tokens=4000,
        timeout_s=30,
        effort="medium",
    )


def _provider(handler: Handler, sent: list[httpx.Request] | None = None) -> ClaudeProvider:
    def record(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return ClaudeProvider("sk-ant-test-fake-key-0000000000", max_retries=0, http_client=client)


def _error(status: int, kind: str) -> Handler:
    body = {"type": "error", "error": {"type": kind, "message": "test"}}
    return lambda _req: httpx.Response(status, json=body)


async def test_success_sends_structured_output_and_caching() -> None:
    sent: list[httpx.Request] = []
    provider = _provider(lambda _r: httpx.Response(200, json=_message(json.dumps(OUTPUT))), sent)
    result = await provider.run(_request())
    assert result.data == OUTPUT
    assert (result.usage.input_tokens, result.usage.cached_input_tokens) == (1200, 900)

    body = json.loads(sent[-1].content)
    assert sent[-1].url.path == "/v1/messages"
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in body  # rejected by the current Claude API


async def test_refusal_is_rejected_with_usage() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=_message("", "refusal")))
    with pytest.raises(ProviderRejected) as info:
        await provider.run(_request())
    assert info.value.refusal is True
    assert info.value.usage.input_tokens == 1200


async def test_max_tokens_cutoff_is_invalid_output() -> None:
    provider = _provider(
        lambda _r: httpx.Response(200, json=_message('{"options": [', "max_tokens"))
    )
    with pytest.raises(ProviderOutputInvalid):
        await provider.run(_request())


async def test_non_json_text_is_invalid_output() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=_message("not json")))
    with pytest.raises(ProviderOutputInvalid):
        await provider.run(_request())


@pytest.mark.parametrize(
    ("status", "kind"), [(429, "rate_limit_error"), (500, "api_error"), (529, "overloaded_error")]
)
async def test_retryable_statuses_are_unavailable(status: int, kind: str) -> None:
    with pytest.raises(ProviderUnavailable):
        await _provider(_error(status, kind)).run(_request())


async def test_bad_request_is_rejected_not_retried() -> None:
    with pytest.raises(ProviderRejected) as info:
        await _provider(_error(400, "invalid_request_error")).run(_request())
    assert info.value.refusal is False


async def test_connection_error_is_unavailable() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderUnavailable):
        await _provider(fail).run(_request())


async def test_unmocked_client_cannot_reach_a_real_provider() -> None:
    """INV-12 safety net: without a mock transport, calls go to a closed local port."""
    provider = ClaudeProvider("sk-ant-test-fake-key-0000000000", max_retries=0)
    with pytest.raises(ProviderUnavailable) as info:
        await provider.run(_request())
    assert info.value.reason == "APIConnectionError"  # not a 401 from a real server
