"""Public request and response envelopes (API_SPEC.md §2)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TenantRef = Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-]{1,64}$")]


class TaskOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality: Literal["best", "best_value"] = "best_value"
    # `async` is a Python keyword; the JSON field name stays "async".
    run_async: bool = Field(default=False, alias="async")
    house_style: Annotated[str, Field(max_length=40)] = "default"


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: Annotated[str, Field(pattern=r"^[a-z_]+\.[a-z_]+$", max_length=64)]
    tenant: TenantRef
    input: dict[str, Any]
    options: TaskOptions = Field(default_factory=TaskOptions)


class Usage(BaseModel):
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: str


class TaskResult(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed"]
    task: str
    task_version: str
    model: str | None = None
    fallback_used: bool = False
    output: dict[str, Any] | None = None
    usage: Usage | None = None


class TenantUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    status: Literal["active", "suspended"] = "active"


class TenantOut(BaseModel):
    tenant: str
    name: str
    status: str


class HouseStyleUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: Annotated[list[Annotated[str, Field(max_length=300)]], Field(max_length=30)] = Field(
        default_factory=list
    )
    always_tags: Annotated[list[Annotated[str, Field(max_length=60)]], Field(max_length=20)] = (
        Field(default_factory=list)
    )
    avoid_words: Annotated[list[Annotated[str, Field(max_length=60)]], Field(max_length=100)] = (
        Field(default_factory=list)
    )
