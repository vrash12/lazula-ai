"""HMAC request signing (ARCHITECTURE.md §5.1, SECURITY.md §4.1).

The signed message is:

    METHOD \n PATH_WITH_QUERY \n TIMESTAMP \n REQUEST_ID \n SHA256_HEX(BODY)

Signing the query string as part of the path means GET parameters cannot be
altered in transit. The same scheme signs webhooks sent back to apps.
"""

from __future__ import annotations

import hashlib
import hmac
import re

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{8,80}$")


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def signing_message(method: str, path: str, timestamp: str, request_id: str, body: bytes) -> bytes:
    return "\n".join([method.upper(), path, timestamp, request_id, body_digest(body)]).encode()


def sign(
    secret: bytes, method: str, path: str, timestamp: str, request_id: str, body: bytes
) -> str:
    message = signing_message(method, path, timestamp, request_id, body)
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify(
    secret: bytes,
    signature: str,
    method: str,
    path: str,
    timestamp: str,
    request_id: str,
    body: bytes,
) -> bool:
    expected = sign(secret, method, path, timestamp, request_id, body)
    return hmac.compare_digest(expected, signature)


def timestamp_is_fresh(timestamp: str, now: float, max_skew_seconds: int) -> bool:
    if not timestamp.isdigit() or len(timestamp) > 12:
        return False
    return abs(now - int(timestamp)) <= max_skew_seconds
