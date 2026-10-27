"""Admin sign-in in two steps, lockout and per-IP limits (SECURITY.md §4.1).

Step 1 checks the email and password; step 2 checks the two-factor code. Every
step-1 failure gives the caller the same answer, so the form never reveals whether
an email belongs to a staff account or whether it is locked. The real reason goes
to the log (without the email) and, for known accounts, to the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import totp
from app.core.crypto import Vault, VaultError
from app.core.logging import get_logger
from app.core.passwords import hash_password, needs_rehash, spend_verify_time, verify_password
from app.models import AdminUser, AuditEvent
from app.settings import Settings

log = get_logger()

# A label used as encryption context, not a secret.
TOTP_SECRET_PURPOSE = "admin_totp:{user_id}"  # noqa: S105  # nosec B105
_IP_KEY = "admin_sign_in_ip:{ip}"


class SignInFailed(Exception):
    """Sign-in refused. `reason` is for logs and audit only, never for the page."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TooManyAttempts(Exception):
    """This address has used up its sign-in attempts for the lockout window."""


@dataclass(frozen=True)
class Clock:
    """Current time in both forms the sign-in needs (naive UTC for MySQL, epoch for codes)."""

    utc: datetime
    epoch: float


def normalize_email(email: str) -> str:
    return email.strip().lower()


def actor(user: AdminUser) -> str:
    return f"admin:{user.id}"


async def check_ip_allowance(redis: Any, ip: str, settings: Settings) -> None:
    """Count this attempt against the caller's address; raise when over the limit."""
    key = _IP_KEY.format(ip=ip)
    attempts = int(await redis.incr(key))
    if attempts == 1:
        await redis.expire(key, settings.admin_lockout_minutes * 60)
    if attempts > settings.admin_sign_in_attempts_per_ip:
        log.warning("admin_sign_in_ip_limited", attempts=attempts)
        raise TooManyAttempts()


async def _record_failure(
    db: AsyncSession, user: AdminUser, reason: str, settings: Settings, clock: Clock
) -> None:
    user.failed_sign_ins += 1
    details: dict[str, Any] = {"reason": reason}
    if user.failed_sign_ins >= settings.admin_max_failed_sign_ins:
        user.locked_until = clock.utc + timedelta(minutes=settings.admin_lockout_minutes)
        user.failed_sign_ins = 0
        details["locked_until"] = user.locked_until.isoformat()
        log.warning("admin_account_locked", admin_id=user.id)
    db.add(
        AuditEvent(
            actor=actor(user), action="admin.sign_in_failed", target=actor(user), details=details
        )
    )
    await db.commit()


def _check_can_sign_in(user: AdminUser, clock: Clock) -> None:
    if user.status != "active":
        raise SignInFailed("inactive")
    if user.locked_until is not None and user.locked_until > clock.utc:
        raise SignInFailed("locked")
    if user.totp_secret_enc is None:
        raise SignInFailed("two_factor_not_set_up")


async def check_password(
    db: AsyncSession, settings: Settings, *, email: str, password: str, clock: Clock
) -> AdminUser:
    """Step 1: return the user whose password matches, or raise SignInFailed.

    Passing this step signs nobody in; it only opens the code step (step 2).
    """
    if not settings.admin_email_allowed(email):
        spend_verify_time(password)
        raise SignInFailed("not_on_allow_list")
    user = await db.scalar(select(AdminUser).where(AdminUser.email == normalize_email(email)))
    if user is None:
        spend_verify_time(password)
        raise SignInFailed("unknown_email")
    try:
        _check_can_sign_in(user, clock)
    except SignInFailed:
        spend_verify_time(password)
        raise
    if not verify_password(user.password_hash, password):
        await _record_failure(db, user, "password", settings, clock)
        raise SignInFailed("password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        await db.commit()
    return user


async def check_code(
    db: AsyncSession, vault: Vault, settings: Settings, *, user_id: int, code: str, clock: Clock
) -> AdminUser:
    """Step 2: finish signing in with a two-factor code, or raise SignInFailed.

    Wrong codes count towards the same lockout as wrong passwords.
    """
    user = await db.get(AdminUser, user_id)
    if user is None:
        raise SignInFailed("unknown_user")
    if not settings.admin_email_allowed(user.email):
        raise SignInFailed("not_on_allow_list")
    _check_can_sign_in(user, clock)
    secret_enc = user.totp_secret_enc
    if secret_enc is None:  # also checked above; repeated so the type is narrowed
        raise SignInFailed("two_factor_not_set_up")
    try:
        secret = vault.decrypt(secret_enc, TOTP_SECRET_PURPOSE.format(user_id=user.id))
    except VaultError as exc:
        log.error("admin_totp_undecryptable", admin_id=user.id)
        raise SignInFailed("two_factor_unreadable") from exc
    step = totp.matching_step(secret, code, clock.epoch, user.totp_last_step)
    if step is None:
        await _record_failure(db, user, "code", settings, clock)
        raise SignInFailed("code")

    user.totp_last_step = step
    user.failed_sign_ins = 0
    user.locked_until = None
    user.last_sign_in_at = clock.utc
    db.add(AuditEvent(actor=actor(user), action="admin.sign_in", target=actor(user), details={}))
    await db.commit()
    return user


def email_hint(email: str) -> str:
    """`ada@lazula.example` → `a•••@lazula.example`, shown on the code step."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain}" if domain else "your account"
