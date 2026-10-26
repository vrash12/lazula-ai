"""Admin password hashing with Argon2id (SECURITY.md §4.1, admin console).

Hashes use the library's recommended Argon2id parameters. When those defaults
change, `needs_rehash` lets a successful sign-in upgrade the stored hash.
"""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # bounds hashing work for a single request

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("not-a-real-password-only-equalizes-timing")


def spend_verify_time(password: str) -> None:
    """Do the same hashing work as a real check, so unknown emails take as long as known ones."""
    verify_password(_dummy_hash(), password)


def password_problem(password: str) -> str | None:
    """A reason the password cannot be used, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters"
    return None
