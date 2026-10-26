"""Admin roles and which screens each role may open (UI-UX.md §3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    OPERATOR = "operator"
    DESIGNER = "designer"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Screen:
    id: str
    title: str
    roles: frozenset[Role]
    milestone: str


_ALL_STAFF = frozenset({Role.OWNER, Role.OPERATOR, Role.VIEWER})
_EDITORS = frozenset({Role.OWNER, Role.OPERATOR})

SCREENS: tuple[Screen, ...] = (
    Screen("ADM-01", "Overview", _ALL_STAFF, "M2"),
    Screen("ADM-02", "Providers and keys", _EDITORS, "M2"),
    Screen("ADM-03", "Models", _ALL_STAFF, "M2"),
    Screen("ADM-04", "Tasks and prompts", _ALL_STAFF, "M2"),
    Screen("ADM-06", "Tenants and budgets", _ALL_STAFF, "M2"),
    Screen("ADM-07", "Usage and cost", _ALL_STAFF, "M2"),
    Screen("ADM-08", "Request log", _EDITORS, "M2"),
    Screen("ADM-09", "Evals", _ALL_STAFF, "M2"),
    Screen("ADM-10", "Apps and secrets", _EDITORS, "M2"),
    Screen("ADM-11", "Audit log", _EDITORS, "M2"),
    Screen("ADM-12", "Admin users", frozenset({Role.OWNER}), "M2"),
    Screen("ADM-13", "Quality and adoption", _ALL_STAFF, "M3"),
    Screen("ADM-14", "Feature switches", _ALL_STAFF, "M2"),
    Screen("ST-01", "Studio documents", frozenset(Role), "M6"),
    Screen("ST-04", "Brand kits and templates", frozenset(Role), "M6"),
)


def screens_for(role: str) -> list[Screen]:
    return [screen for screen in SCREENS if role in screen.roles]
