"""`sermon.captions`: caption options for a sermon idea or any post idea (API_SPEC.md §4.1)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.tasks.base import HouseStyleData, TaskDefinition, chain
from app.tasks.common import Platform, check_avoid_words, merge_tags


class CaptionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: Annotated[str, Field(min_length=20, max_length=20_000)]
    count: Annotated[int, Field(ge=1, le=5)] = 3
    platform_hint: Platform | None = None


class CaptionOption(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=80)]
    caption: Annotated[str, Field(min_length=1, max_length=2_200)]
    hashtags: Annotated[list[str], Field(min_length=1, max_length=20)]


class CaptionsOutput(BaseModel):
    options: Annotated[list[CaptionOption], Field(min_length=1, max_length=5)]


def _postprocess(
    output: CaptionsOutput, task_input: CaptionsInput, style: HouseStyleData
) -> CaptionsOutput:
    options = output.options[: task_input.count]
    for option in options:
        check_avoid_words([option.title, option.caption], style)
        option.hashtags = merge_tags(option.hashtags, style)
    return CaptionsOutput(options=options)


def _example() -> CaptionsOutput:
    return CaptionsOutput(
        options=[
            CaptionOption(
                title="Hope that holds",
                caption=(
                    "When the week feels heavy, hope is not a feeling. "
                    "It's a promise. Join us Sunday."
                ),
                hashtags=["#hope", "#sundayservice", "#faith"],
            ),
            CaptionOption(
                title="A question for you",
                caption=(
                    "What would change if you believed you were truly known? "
                    "This Sunday we find out."
                ),
                hashtags=["#known", "#church", "#community"],
            ),
            CaptionOption(
                title="Come as you are",
                caption="No dress code. No pretending. Just a seat saved for you this Sunday.",
                hashtags=["#comeasyouare", "#church", "#welcome"],
            ),
        ]
    )


TASK = TaskDefinition(
    name="sermon.captions",
    version="1",
    input_model=CaptionsInput,
    output_model=CaptionsOutput,
    prompt="sermon.captions/v1.md",
    chains={
        "best": chain("anthropic:claude-opus-5-5"),
        "best_value": chain("anthropic:claude-opus-5-5"),
    },
    max_tokens=4_000,
    timeout_s=45,
    effort="medium",
    example=_example,
    postprocess=_postprocess,
)
