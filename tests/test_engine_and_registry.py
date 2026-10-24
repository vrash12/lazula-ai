"""Engine edge cases and loading providers from the encrypted vault."""

import pytest

from app.core.engine import run_task
from app.core.errors import EngineFailure, ErrorCode
from app.models import ProviderAccount
from app.providers.claude import ClaudeProvider
from app.providers.registry import PROVIDER_KEY_PURPOSE, ProviderRegistry, build_registry
from app.tasks import TASKS
from app.tasks.base import HouseStyleData
from tests.conftest import CAPTION_INPUT, Hub

CAPTIONS = TASKS["sermon.captions"]


async def test_bad_request_stops_the_chain(hub: Hub) -> None:
    hub.fake.script("primary", "reject")
    response = await hub.run_task("sermon.captions", CAPTION_INPUT)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "output_invalid"
    assert [c.model for c in hub.fake.calls] == ["primary"]  # secondary never tried


async def test_no_configured_provider_means_ai_unavailable() -> None:
    task_input = CAPTIONS.input_model.model_validate(CAPTION_INPUT)
    with pytest.raises(EngineFailure) as info:
        await run_task(
            CAPTIONS,
            task_input,
            quality="best_value",
            house_style=HouseStyleData(),
            registry=ProviderRegistry(),
            enable_fake=False,
        )
    assert info.value.code == ErrorCode.AI_UNAVAILABLE
    assert info.value.attempts == []


async def test_registry_loads_active_keys_from_the_vault(hub: Hub) -> None:
    vault = hub.asgi.state.vault
    async with hub.asgi.state.sessionmaker() as session:
        session.add(
            ProviderAccount(
                provider="anthropic",
                key_enc=vault.encrypt(
                    "sk-ant-test-fake-key-0000000000",
                    PROVIDER_KEY_PURPOSE.format(provider="anthropic"),
                ),
                key_last4="0000",
            )
        )
        session.add(
            ProviderAccount(
                provider="openai",
                key_enc=vault.encrypt("sk-test", PROVIDER_KEY_PURPOSE.format(provider="openai")),
                key_last4="test",
            )
        )
        await session.commit()
        registry = await build_registry(session, vault, enable_fake=False)
    assert isinstance(registry.get("anthropic"), ClaudeProvider)
    assert registry.get("openai") is None  # no adapter yet; logged, not crashed
    assert registry.get("fake") is None
