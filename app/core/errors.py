"""Error types and their mapping to the public error codes (ARCHITECTURE.md §5.4).

Messages here are shown to apps. They must never contain client content,
provider error bodies or secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.engine import Attempt


class ErrorCode(StrEnum):
    INVALID_SIGNATURE = "invalid_signature"
    REPLAYED_REQUEST = "replayed_request"
    TASK_NOT_ALLOWED = "task_not_allowed"
    TENANT_NOT_FOUND = "tenant_not_found"
    INVALID_INPUT = "invalid_input"
    BUDGET_EXCEEDED = "budget_exceeded"
    RATE_LIMITED = "rate_limited"
    AI_UNAVAILABLE = "ai_unavailable"
    OUTPUT_INVALID = "output_invalid"
    REFUSED = "refused"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_SIGNATURE: 401,
    ErrorCode.REPLAYED_REQUEST: 409,
    ErrorCode.TASK_NOT_ALLOWED: 403,
    ErrorCode.TENANT_NOT_FOUND: 404,
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.BUDGET_EXCEEDED: 402,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.AI_UNAVAILABLE: 503,
    ErrorCode.OUTPUT_INVALID: 502,
    ErrorCode.REFUSED: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTERNAL_ERROR: 500,
}


class LazulaError(Exception):
    """An error that becomes a public `{"error": {...}}` response."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    default_message = "Something went wrong"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]


class InvalidSignature(LazulaError):
    code = ErrorCode.INVALID_SIGNATURE
    default_message = "Request signature, app or timestamp is invalid"


class ReplayedRequest(LazulaError):
    code = ErrorCode.REPLAYED_REQUEST
    default_message = "This request ID was already used for a different request"


class TaskNotAllowed(LazulaError):
    code = ErrorCode.TASK_NOT_ALLOWED
    default_message = "This app may not run this task"


class TenantNotFound(LazulaError):
    code = ErrorCode.TENANT_NOT_FOUND
    default_message = "Unknown tenant for this app"


class InvalidInput(LazulaError):
    code = ErrorCode.INVALID_INPUT
    default_message = "The request input is invalid"


class NotFound(LazulaError):
    code = ErrorCode.NOT_FOUND
    default_message = "Not found"


_ENGINE_MESSAGES = {
    ErrorCode.AI_UNAVAILABLE: "AI suggestions are unavailable right now; try again later",
    ErrorCode.OUTPUT_INVALID: "No model returned a valid answer",
    ErrorCode.REFUSED: "The AI models declined this content",
}


class EngineFailure(LazulaError):
    """The model chain finished without a valid result. Carries the attempts for the ledger."""

    def __init__(self, failure_code: ErrorCode, attempts: list[Attempt] | None = None) -> None:
        self.code = failure_code
        self.attempts = attempts or []
        super().__init__(_ENGINE_MESSAGES.get(failure_code))


# ---- Provider-level errors (internal; never shown to apps directly) ----


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0


class ProviderError(Exception):
    """Base for errors raised by provider adapters. `usage` is set when tokens were consumed."""

    def __init__(self, reason: str, usage: TokenUsage | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.usage = usage or TokenUsage()


class ProviderUnavailable(ProviderError):
    """Retryable: rate limits, 5xx, timeouts, connection errors. The chain moves on."""


class ProviderRejected(ProviderError):
    """Not retryable with the same input (400-class) unless `refusal` is set."""

    def __init__(self, reason: str, usage: TokenUsage | None = None, *, refusal: bool = False):
        super().__init__(reason, usage)
        self.refusal = refusal


class ProviderOutputInvalid(ProviderError):
    """The provider answered, but the answer was cut off or not valid JSON."""
