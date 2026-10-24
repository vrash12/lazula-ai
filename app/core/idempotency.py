"""Replay protection and idempotent retries (ARCHITECTURE.md §5, BEST_PRACTICES.md §3).

Keyed by app and `X-Lazula-Request-Id`:
- first use: marked "running";
- same ID and same body while running: 409 (still running);
- same ID and same body when done: the stored response is returned, nothing is re-run or re-billed;
- same ID with a different body: 409 replayed_request.

Completed responses are kept for `RESULT_CACHE_TTL_SECONDS` (24 h by default),
matching the retention for results in SECURITY.md §4.3. Failed requests release
the key so the app can retry with the same ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.errors import ReplayedRequest


class RedisLike(Protocol):
    async def set(self, name: str, value: str, ex: int | None = ..., nx: bool = ...) -> Any: ...
    async def get(self, name: str) -> Any: ...
    async def delete(self, *names: str) -> Any: ...


@dataclass
class CachedResponse:
    status_code: int
    body: dict[str, Any]


def _key(app_id: int, request_id: str) -> str:
    return f"idem:{app_id}:{request_id}"


async def begin(
    redis: RedisLike, app_id: int, request_id: str, digest: str, running_ttl: int
) -> CachedResponse | None:
    """Claim the request ID. Returns a cached response if this exact request already completed."""
    key = _key(app_id, request_id)
    claimed = await redis.set(
        key, json.dumps({"digest": digest, "state": "running"}), ex=running_ttl, nx=True
    )
    if claimed:
        return None
    raw = await redis.get(key)
    if raw is None:  # expired between calls; claim again
        await redis.set(key, json.dumps({"digest": digest, "state": "running"}), ex=running_ttl)
        return None
    entry = json.loads(raw)
    if entry["digest"] != digest:
        raise ReplayedRequest()
    if entry["state"] == "running":
        raise ReplayedRequest("A request with this ID is still running")
    return CachedResponse(status_code=entry["status_code"], body=entry["body"])


async def complete(
    redis: RedisLike,
    app_id: int,
    request_id: str,
    digest: str,
    status_code: int,
    body: dict[str, Any],
    ttl: int,
) -> None:
    value = {"digest": digest, "state": "done", "status_code": status_code, "body": body}
    await redis.set(_key(app_id, request_id), json.dumps(value), ex=ttl)


async def release(redis: RedisLike, app_id: int, request_id: str) -> None:
    await redis.delete(_key(app_id, request_id))
