"""Shared fixtures: in-memory SQLite, fake Redis, the fake provider and signed clients.

No test calls a real AI provider (INV-12).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import fakeredis
import httpx
import pytest

os.environ.update(
    LAZULA_ENV="test",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    REDIS_URL="redis://localhost:6379/15",
    MASTER_KEY="dGVzdC1vbmx5LWZha2UtbWFzdGVyLWtleS0zMmJ5dGU=",  # fake, test only
    ENABLE_FAKE_PROVIDER="true",
    LOG_LEVEL="INFO",
    # Safety net for INV-12: if any test ever builds a real SDK client without a mock
    # transport, its requests go to a closed local port instead of a provider.
    ANTHROPIC_BASE_URL="http://127.0.0.1:9",
    ANTHROPIC_API_KEY="",
)

from app.api.deps import APP_SECRET_PURPOSE
from app.core import signing
from app.main import create_app, init_state
from app.models import AIModel, App, Base, Tenant
from app.providers.fake import FakeProvider
from app.providers.registry import ProviderRegistry
from app.settings import Settings
from app.tasks import example_for_output

ALL_TASKS = ["sermon.captions", "post.platform_versions"]


@dataclass
class AppCreds:
    slug: str
    secret: str


@dataclass
class Hub:
    client: httpx.AsyncClient
    asgi: Any
    fake: FakeProvider
    apps: dict[str, AppCreds]

    async def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        app: str = "sermonpost",
        request_id: str | None = None,
        timestamp: int | None = None,
        secret: str | None = None,
        raw_body: bytes | None = None,
    ) -> httpx.Response:
        creds = self.apps[app]
        payload = (
            raw_body
            if raw_body is not None
            else (json.dumps(body).encode() if body is not None else b"")
        )
        rid = request_id or f"rq_{secrets.token_hex(8)}"
        ts = str(timestamp if timestamp is not None else int(time.time()))
        signature = signing.sign((secret or creds.secret).encode(), method, path, ts, rid, payload)
        headers = {
            "Content-Type": "application/json",
            "X-Lazula-App": creds.slug,
            "X-Lazula-Timestamp": ts,
            "X-Lazula-Request-Id": rid,
            "X-Lazula-Signature": signature,
        }
        return await self.client.request(method, path, content=payload, headers=headers)

    async def run_task(
        self, task: str, task_input: dict[str, Any], **kwargs: Any
    ) -> httpx.Response:
        tenant = kwargs.pop("tenant", "church_1")
        options = kwargs.pop("options", {})
        return await self.call(
            "POST",
            "/v1/tasks",
            {"task": task, "tenant": tenant, "input": task_input, "options": options},
            **kwargs,
        )


CAPTION_INPUT = {
    "context": "Sermon on hope in hard seasons, from Romans 5. Invite people to Sunday."
}


@pytest.fixture
async def hub() -> AsyncIterator[Hub]:
    settings = Settings()  # type: ignore[call-arg]
    asgi = create_app(settings, manage_state=False)
    registry = ProviderRegistry()
    fake = FakeProvider(example_for_output)
    registry.add(fake)
    await init_state(
        asgi,
        settings,
        redis_client=fakeredis.FakeAsyncRedis(decode_responses=True),
        providers=registry,
    )

    async with asgi.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    apps: dict[str, AppCreds] = {}
    async with asgi.state.sessionmaker() as session:
        for slug, tenant_ref in (("sermonpost", "church_1"), ("mediateam", "client_1")):
            secret = secrets.token_urlsafe(32)
            row = App(
                slug=slug,
                name=slug,
                secret_enc=asgi.state.vault.encrypt(secret, APP_SECRET_PURPOSE.format(slug=slug)),
                allowed_tasks=ALL_TASKS,
            )
            session.add(row)
            await session.flush()
            session.add(Tenant(app_id=row.id, external_ref=tenant_ref, name=tenant_ref))
            apps[slug] = AppCreds(slug, secret)
        for model in ("primary", "secondary"):
            session.add(
                AIModel(
                    provider="fake",
                    model_id=model,
                    input_price=Decimal("1"),
                    output_price=Decimal("2"),
                    cached_input_price=Decimal("0.1"),
                    cache_write_price=Decimal("1.25"),
                )
            )
        await session.commit()

    transport = httpx.ASGITransport(app=asgi)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        yield Hub(client=client, asgi=asgi, fake=fake, apps=apps)
    await asgi.state.engine.dispose()
