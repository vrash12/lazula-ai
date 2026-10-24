"""`post.platform_versions`: tailor one approved caption to each platform (API_SPEC.md §4.1)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.tasks.base import HouseStyleData, PostCheckFailed, TaskDefinition, chain
from app.tasks.common import Platform, check_avoid_words, merge_tags


class PlatformVersionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(max_length=300)] | None = None
    caption: Annotated[str, Field(min_length=1, max_length=5_000)]
    platforms: Annotated[list[Platform], Field(min_length=1, max_length=8)]
    platform_rules: dict[Platform, Annotated[str, Field(max_length=1_000)]] = Field(
        default_factory=dict
    )


class PlatformVersion(BaseModel):
    platform: Platform
    text: Annotated[str, Field(min_length=1, max_length=5_000)]
    hashtags: Annotated[list[str], Field(max_length=30)] = Field(default_factory=list)


class PlatformVersionsOutput(BaseModel):
    versions: Annotated[list[PlatformVersion], Field(min_length=1, max_length=8)]


_NO_HASHTAG_PLATFORMS = {Platform.google_business, Platform.website, Platform.podcast}


def _postprocess(
    output: PlatformVersionsOutput, task_input: PlatformVersionsInput, style: HouseStyleData
) -> PlatformVersionsOutput:
    by_platform = {v.platform: v for v in output.versions}
    if set(task_input.platforms) - set(by_platform):
        raise PostCheckFailed("platform_missing")
    # Versions for platforms that were not requested are dropped.
    ordered = []
    for platform in task_input.platforms:
        version = by_platform[platform]
        check_avoid_words([version.text], style)
        version.hashtags = (
            [] if platform in _NO_HASHTAG_PLATFORMS else merge_tags(version.hashtags, style)
        )
        ordered.append(version)
    return PlatformVersionsOutput(versions=ordered)


def _example() -> PlatformVersionsOutput:
    return PlatformVersionsOutput(
        versions=[
            PlatformVersion(platform=p, text=f"Sample {p.value} version.", hashtags=["#church"])
            for p in Platform
        ]
    )


TASK = TaskDefinition(
    name="post.platform_versions",
    version="1",
    input_model=PlatformVersionsInput,
    output_model=PlatformVersionsOutput,
    prompt="post.platform_versions/v1.md",
    chains={
        "best": chain("anthropic:claude-opus-5-5"),
        "best_value": chain("anthropic:claude-opus-5-5"),
    },
    max_tokens=6_000,
    timeout_s=45,
    effort="medium",
    example=_example,
    postprocess=_postprocess,
)
