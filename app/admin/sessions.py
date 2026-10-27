"""Server-side admin sessions in Redis (SECURITY.md §4.1).

The browser holds only a random token in an HttpOnly cookie. Redis stores the
session under a SHA-256 of that token, so a copy of Redis cannot be replayed as
a cookie. Sessions end after 30 minutes without activity (sliding expiry) and
12 hours after sign-in regardless of activity. Between the password step and the
code step, a separate short-lived "pending" record holds the user ID.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any

from app.settings import Settings

_KEY = "admin_session:{digest}"


@dataclass(frozen=True)
class AdminSession:
    user_id: int
    csrf_token: str
    created_at: float


def _key(token: str) -> str:
    return _KEY.format(digest=hashlib.sha256(token.encode()).hexdigest())


def _idle_seconds(settings: Settings) -> int:
    return settings.admin_session_idle_minutes * 60


async def create_session(
    redis: Any, user_id: int, settings: Settings, now: float
) -> tuple[str, AdminSession]:
    """Start a new session. A fresh token every sign-in prevents session fixation."""
    token = secrets.token_urlsafe(32)
    session = AdminSession(user_id=user_id, csrf_token=secrets.token_urlsafe(32), created_at=now)
    payload = {"user_id": user_id, "csrf": session.csrf_token, "created_at": now}
    await redis.set(_key(token), json.dumps(payload), ex=_idle_seconds(settings))
    return token, session


async def load_session(
    redis: Any, token: str | None, settings: Settings, now: float
) -> AdminSession | None:
    """Return the live session for `token` and extend its idle timer, or None."""
    if not token or len(token) > 100:
        return None
    key = _key(token)
    raw = await redis.get(key)
    if raw is None:
        return None
    data = json.loads(raw)
    if now - float(data["created_at"]) > settings.admin_session_max_hours * 3600:
        await redis.delete(key)
        return None
    await redis.expire(key, _idle_seconds(settings))
    return AdminSession(
        user_id=int(data["user_id"]), csrf_token=str(data["csrf"]), created_at=data["created_at"]
    )


async def end_session(redis: Any, token: str | None) -> None:
    if token:
        await redis.delete(_key(token))


# ---- Between the password step and the code step ----

_PENDING_KEY = "admin_pending:{digest}"
PENDING_SECONDS = 300  # the code step must be finished within 5 minutes


@dataclass(frozen=True)
class PendingSignIn:
    """A password that matched, waiting for its two-factor code. Grants no access."""

    user_id: int
    email_hint: str
    csrf_token: str


def _pending_key(token: str) -> str:
    return _PENDING_KEY.format(digest=hashlib.sha256(token.encode()).hexdigest())


async def start_pending(redis: Any, user_id: int, email_hint: str) -> tuple[str, PendingSignIn]:
    token = secrets.token_urlsafe(32)
    pending = PendingSignIn(user_id, email_hint, secrets.token_urlsafe(32))
    payload = {"user_id": user_id, "hint": email_hint, "csrf": pending.csrf_token}
    await redis.set(_pending_key(token), json.dumps(payload), ex=PENDING_SECONDS)
    return token, pending


async def load_pending(redis: Any, token: str | None) -> PendingSignIn | None:
    if not token or len(token) > 100:
        return None
    raw = await redis.get(_pending_key(token))
    if raw is None:
        return None
    data = json.loads(raw)
    return PendingSignIn(int(data["user_id"]), str(data["hint"]), str(data["csrf"]))


async def end_pending(redis: Any, token: str | None) -> None:
    if token:
        await redis.delete(_pending_key(token))
