"""Prompt files and rendering (PROMPTS.md §1).

Each prompt file has YAML-like front matter, then a `# system` part (stable,
cacheable, no request data) and a `# user` part (a template with data
sections). Every `{{ value }}` is client data, so it is escaped: angle
brackets become entities, which stops content from closing or faking the
data tags around it (prompt-injection hygiene, SECURITY.md §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

PROMPTS_DIR = Path(__file__).parent
_USER_MARKER = "\n# user\n"


def _escape_data(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return value


# Autoescape is off on purpose: prompts are plain text, not HTML. Data is escaped by
# `finalize` above instead, which applies to every {{ }} expression.
_env = SandboxedEnvironment(
    loader=FileSystemLoader(PROMPTS_DIR),
    autoescape=False,
    undefined=StrictUndefined,
    finalize=_escape_data,
    keep_trailing_newline=True,
    trim_blocks=True,
)


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str


def _strip_front_matter(source: str) -> str:
    if source.startswith("---\n"):
        end = source.find("\n---\n", 4)
        if end == -1:
            raise ValueError("Unclosed front matter")
        return source[end + 5 :]
    return source


def render(prompt_path: str, **context: Any) -> RenderedPrompt:
    raw = (PROMPTS_DIR / prompt_path).read_text(encoding="utf-8")
    body = _strip_front_matter(raw)
    text = _env.from_string(body).render(**context)
    if not text.startswith("# system\n") or _USER_MARKER not in text:
        raise ValueError(f"Prompt {prompt_path} must have '# system' and '# user' parts")
    system, _, user = text[len("# system\n") :].partition(_USER_MARKER)
    return RenderedPrompt(system=system.strip(), user=user.strip())
