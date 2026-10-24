"""Builds provider instances from the encrypted key vault at start-up."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import Vault
from app.core.logging import get_logger
from app.models import ProviderAccount
from app.providers.base import Provider
from app.providers.claude import ClaudeProvider
from app.providers.fake import FakeProvider
from app.tasks import example_for_output

log = get_logger()

PROVIDER_KEY_PURPOSE = "provider_key:{provider}"


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def add(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def names(self) -> set[str]:
        return set(self._providers)

    @property
    def fake(self) -> FakeProvider | None:
        provider = self._providers.get("fake")
        return provider if isinstance(provider, FakeProvider) else None


async def build_registry(
    session: AsyncSession, vault: Vault, *, enable_fake: bool
) -> ProviderRegistry:
    registry = ProviderRegistry()
    accounts = (
        await session.scalars(select(ProviderAccount).where(ProviderAccount.status == "active"))
    ).all()
    for account in accounts:
        key = vault.decrypt(account.key_enc, PROVIDER_KEY_PURPOSE.format(provider=account.provider))
        if account.provider == "anthropic":
            registry.add(ClaudeProvider(key))
        else:
            log.warning("provider_adapter_missing", provider=account.provider)
    if enable_fake:
        registry.add(FakeProvider(example_for_output))
    log.info("providers_loaded", providers=sorted(registry.names()))
    return registry
