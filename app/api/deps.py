"""Request dependencies: database session, Redis, and signed-app authentication."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import signing
from app.core.crypto import Vault, VaultError
from app.core.errors import InvalidSignature
from app.core.logging import get_logger
from app.models import App
from app.settings import Settings

log = get_logger()

# A label used as encryption context, not a secret.
APP_SECRET_PURPOSE = "app_secret:{slug}"  # noqa: S105  # nosec B105


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_redis(request: Request) -> Any:
    return request.app.state.redis


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_vault(request: Request) -> Vault:
    vault: Vault = request.app.state.vault
    return vault


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


@dataclass
class SignedRequest:
    app: App
    request_id: str
    body: bytes
    digest: str


def _app_secrets(app: App, vault: Vault) -> list[bytes]:
    purpose = APP_SECRET_PURPOSE.format(slug=app.slug)
    secrets: list[bytes] = []
    for token in (app.secret_enc, app.secret_next_enc):
        if token:
            try:
                secrets.append(vault.decrypt(token, purpose).encode())
            except VaultError:
                log.error("app_secret_undecryptable", app=app.slug)
    return secrets


async def signed_app(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    vault: Annotated[Vault, Depends(get_vault)],
) -> SignedRequest:
    """Authenticate an app request (INV-07). Every failure looks the same to the caller."""
    slug = request.headers.get("x-lazula-app", "")
    timestamp = request.headers.get("x-lazula-timestamp", "")
    request_id = request.headers.get("x-lazula-request-id", "")
    signature = request.headers.get("x-lazula-signature", "")

    if not (slug and signature and signing.REQUEST_ID_PATTERN.match(request_id)):
        raise InvalidSignature()
    if not signing.timestamp_is_fresh(timestamp, time.time(), settings.signature_max_skew_seconds):
        raise InvalidSignature()

    app = await session.scalar(select(App).where(App.slug == slug, App.status == "active"))
    if app is None:
        raise InvalidSignature()

    body = await request.body()
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    valid = any(
        signing.verify(secret, signature, request.method, path, timestamp, request_id, body)
        for secret in _app_secrets(app, vault)
    )
    if not valid:
        log.warning("signature_invalid", app=slug)
        raise InvalidSignature()

    request.state.app_slug = slug
    request.state.request_id = request_id
    return SignedRequest(
        app=app, request_id=request_id, body=body, digest=signing.body_digest(body)
    )


SignedDep = Annotated[SignedRequest, Depends(signed_app)]
