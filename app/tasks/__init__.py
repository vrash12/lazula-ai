"""Task registry. Add a task: see AGENTS.md §7."""

from __future__ import annotations

from pydantic import BaseModel

from app.tasks import post_platform_versions, sermon_captions
from app.tasks.base import TaskDefinition

TASKS: dict[str, TaskDefinition] = {
    t.name: t for t in (sermon_captions.TASK, post_platform_versions.TASK)
}

_EXAMPLES = {t.output_model: t.example for t in TASKS.values()}


def get_task(name: str) -> TaskDefinition | None:
    return TASKS.get(name)


def example_for_output(output_model: type[BaseModel]) -> BaseModel:
    """A schema-valid example answer for an output model (used by the fake provider)."""
    return _EXAMPLES[output_model]()
