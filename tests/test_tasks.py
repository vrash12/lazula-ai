"""Task execution, fallback, validation, house style and the ledger."""

from decimal import Decimal

from sqlalchemy import select

from app.core.errors import TokenUsage
from app.core.ledger import cost_of
from app.models import AIModel, RequestRecord, UsageEntry
from tests.conftest import CAPTION_INPUT, Hub


async def _ledger(hub: Hub) -> list[UsageEntry]:
    async with hub.asgi.state.sessionmaker() as session:
        return list((await session.scalars(select(UsageEntry).order_by(UsageEntry.id))).all())


async def test_captions_succeed_with_schema_valid_output_and_cost(hub: Hub) -> None:
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["model"] == "fake:primary"
    assert body["fallback_used"] is False
    assert len(body["output"]["options"]) == 3
    # 200 uncached x $1 + 800 cached x $0.10 + 200 output x $2, per million tokens
    assert body["usage"]["cost_usd"] == "0.0007"
    ledger = await _ledger(hub)
    assert [(e.model, e.outcome) for e in ledger] == [("primary", "ok")]
    async with hub.asgi.state.sessionmaker() as session:
        record = await session.scalar(select(RequestRecord))
        assert record is not None and record.status == "completed" and record.attempts == 1


async def test_fallback_to_next_model_when_first_is_unavailable(hub: Hub) -> None:
    hub.fake.script("primary", "unavailable")
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 200
    assert response.json()["model"] == "fake:secondary"
    assert response.json()["fallback_used"] is True
    assert [(e.model, e.outcome) for e in await _ledger(hub)] == [
        ("primary", "unavailable"),
        ("secondary", "ok"),
    ]


async def test_invalid_output_moves_to_next_model_and_is_billed(hub: Hub) -> None:
    hub.fake.script("primary", {"options": [{"title": "x"}]})  # missing fields
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 200
    ledger = await _ledger(hub)
    assert [e.outcome for e in ledger] == ["output_invalid", "ok"]
    assert ledger[0].cost > 0  # tokens were spent on the bad answer


async def test_all_models_unavailable_returns_ai_unavailable(hub: Hub) -> None:
    hub.fake.script("primary", "unavailable")
    hub.fake.script("secondary", "unavailable")
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"
    async with hub.asgi.state.sessionmaker() as session:
        record = await session.scalar(select(RequestRecord))
        assert (
            record is not None
            and record.status == "failed"
            and record.error_code == "ai_unavailable"
        )


async def test_two_refusals_return_refused(hub: Hub) -> None:
    hub.fake.script("primary", "refuse")
    hub.fake.script("secondary", "refuse")
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "refused"


async def test_failed_request_can_be_retried_with_the_same_id(hub: Hub) -> None:
    hub.fake.script("primary", "unavailable")
    hub.fake.script("secondary", "unavailable")
    first = await hub.run_task("sermon.captions", CAPTION_INPUT, request_id="rq_retry_after_fail")
    second = await hub.run_task("sermon.captions", CAPTION_INPUT, request_id="rq_retry_after_fail")
    assert first.status_code == 503
    assert second.status_code == 200
    attempts = [e.attempt for e in await _ledger(hub)]
    assert attempts == [1, 2, 3]  # numbering continues across the retry


async def test_invalid_input_reports_fields_without_echoing_values(hub: Hub) -> None:
    secret_text = "SENTINEL-short"
    response = await hub.run_task("sermon.captions", {"context": secret_text, "count": 9})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_input"
    assert "context" in error["message"] and "count" in error["message"]
    assert secret_text not in response.text


async def test_house_style_tags_are_added_and_avoid_words_enforced(hub: Hub) -> None:
    style = await hub.call(
        "PUT",
        "/v1/tenants/church_1/house-style",
        {"rules": ["No em dashes"], "always_tags": ["GraceChurch"], "avoid_words": ["—"]},
    )
    assert style.status_code == 204
    dash = {"options": [{"title": "t", "caption": "Hope — always", "hashtags": ["#hope"]}]}
    hub.fake.script("primary", dash)  # breaks the avoid-word rule, so the next model is used
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 200
    assert response.json()["model"] == "fake:secondary"
    for option in response.json()["output"]["options"]:
        assert "#GraceChurch" in option["hashtags"]
        assert "—" not in option["caption"]


async def test_platform_versions_returns_only_requested_platforms_in_order(hub: Hub) -> None:
    response = await hub.run_task(
        "post.platform_versions",
        {
            "caption": "Join us Sunday at 10 for a message on hope.",
            "platforms": ["x", "facebook", "website"],
        },
    )
    assert response.status_code == 200, response.text
    versions = response.json()["output"]["versions"]
    assert [v["platform"] for v in versions] == ["x", "facebook", "website"]
    assert versions[2]["hashtags"] == []  # no hashtags on website copy


async def test_async_option_is_not_available_yet(hub: Hub) -> None:
    response = await hub.run_task("sermon.captions", CAPTION_INPUT, options={"async": True})
    assert response.status_code == 422


def test_cost_calculation_uses_cached_and_cache_write_prices() -> None:
    price = AIModel(
        provider="anthropic",
        model_id="m",
        input_price=Decimal("4"),
        output_price=Decimal("20"),
        cached_input_price=Decimal("0.2"),
        cache_write_price=Decimal("5"),
    )
    usage = TokenUsage(
        input_tokens=4000, cached_input_tokens=3000, cache_write_tokens=500, output_tokens=600
    )
    # 500 uncached x 4 + 3000 x 0.2 + 500 x 5 + 600 x 20 = 2000 + 600 + 2500 + 12000 = 17100 / 1M
    assert cost_of(usage, price) == Decimal("0.01710000")
    assert cost_of(usage, None) == Decimal(0)
