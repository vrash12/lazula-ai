"""Two-factor codes for admin sign-in (RFC 6238: 6 digits, 30-second steps).

A code is accepted for the current step and one step either side, to allow for
clock drift. Each step can be used once: the matched step is stored and any
code for that step or an earlier one is refused, so a code seen over someone's
shoulder cannot be replayed.
"""

from __future__ import annotations

import hmac

import pyotp

ISSUER = "LAZULA.ai"
STEP_SECONDS = 30


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    """The `otpauth://` link authenticator apps import (also what a QR code encodes)."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def matching_step(secret: str, code: str, now: float, last_used_step: int | None) -> int | None:
    """Return the time step `code` belongs to, or None if it is wrong, expired or already used."""
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = int(now // STEP_SECONDS)
    for step in (current - 1, current, current + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None
