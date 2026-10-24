"""Public identifiers and time helpers."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime


def new_task_id() -> str:
    """Time-ordered, unguessable public ID: `req_` + 12 hex ms timestamp + 16 random hex."""
    return f"req_{int(time.time() * 1000):012x}{secrets.token_hex(8)}"


def utcnow() -> datetime:
    """Naive UTC timestamp for MySQL DATETIME columns (stored in UTC, INV-13)."""
    return datetime.now(UTC).replace(tzinfo=None)
