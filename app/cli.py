"""Operator commands until the admin console exists (M2).

    uv run python -m app.cli create-app sermonpost --name SermonPost
        --tasks sermon.captions,post.platform_versions
    uv run python -m app.cli rotate-app-secret sermonpost
    uv run python -m app.cli retire-old-secret sermonpost
    uv run python -m app.cli add-provider-key anthropic      (reads the key from a hidden prompt)
    uv run python -m app.cli seed-models

Secrets are printed once and never logged. Every change writes an audit event.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import APP_SECRET_PURPOSE
from app.core.crypto import Vault, last4, new_app_secret
from app.db import make_engine, make_sessionmaker
from app.models import AIModel, App, AuditEvent, ProviderAccount
from app.providers.registry import PROVIDER_KEY_PURPOSE
from app.settings import get_settings
from app.tasks import TASKS

# Anthropic list prices per million tokens (USD), October 2026. Re-check before relying
# on them (COST_AND_PERFORMANCE.md §3.2). Cache writes are priced at 1.25x input as a
# conservative placeholder until confirmed.
SEED_MODELS: list[tuple[str, str, str, str, str]] = [
    # provider, model, input, output, cached input
    ("anthropic", "claude-opus-5-5", "4.00", "20.00", "0.20"),
    ("anthropic", "claude-sonnet-5-5", "2.00", "10.00", "0.20"),
    ("anthropic", "claude-haiku-5-5", "0.10", "0.50", "0.01"),
    ("fake", "primary", "1.00", "2.00", "0.10"),
    ("fake", "secondary", "1.00", "2.00", "0.10"),
]


def _actor() -> str:
    return f"cli:{getpass.getuser()}"


async def _app_by_slug(session: AsyncSession, slug: str) -> App:
    app = await session.scalar(select(App).where(App.slug == slug))
    if app is None:
        sys.exit(f"No app with slug {slug!r}")
    return app


async def create_app_cmd(session: AsyncSession, vault: Vault, args: argparse.Namespace) -> None:
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [t for t in tasks if t not in TASKS]
    if unknown:
        sys.exit(f"Unknown tasks: {', '.join(unknown)}")
    if await session.scalar(select(App).where(App.slug == args.slug)):
        sys.exit(f"App {args.slug!r} already exists")
    secret = new_app_secret()
    session.add(
        App(
            slug=args.slug,
            name=args.name,
            secret_enc=vault.encrypt(secret, APP_SECRET_PURPOSE.format(slug=args.slug)),
            callback_url=args.callback_url,
            allowed_tasks=tasks,
        )
    )
    session.add(
        AuditEvent(
            actor=_actor(), action="app.create", target=f"app:{args.slug}", details={"tasks": tasks}
        )
    )
    await session.commit()
    print(
        f"App {args.slug!r} created. Its signing secret "
        "(shown once; store it in the app's secret storage):"
    )
    print(secret)


async def rotate_secret_cmd(session: AsyncSession, vault: Vault, args: argparse.Namespace) -> None:
    app = await _app_by_slug(session, args.slug)
    secret = new_app_secret()
    app.secret_next_enc = vault.encrypt(secret, APP_SECRET_PURPOSE.format(slug=app.slug))
    session.add(
        AuditEvent(actor=_actor(), action="app.secret_rotate", target=f"app:{app.slug}", details={})
    )
    await session.commit()
    print("New secret (shown once). Both old and new secrets work until you run retire-old-secret:")
    print(secret)


async def retire_secret_cmd(session: AsyncSession, vault: Vault, args: argparse.Namespace) -> None:
    app = await _app_by_slug(session, args.slug)
    if not app.secret_next_enc:
        sys.exit("No new secret to promote; run rotate-app-secret first")
    app.secret_enc, app.secret_next_enc = app.secret_next_enc, None
    session.add(
        AuditEvent(
            actor=_actor(), action="app.secret_retire_old", target=f"app:{app.slug}", details={}
        )
    )
    await session.commit()
    print("Old secret retired. Only the new secret works now.")


async def add_provider_key_cmd(
    session: AsyncSession, vault: Vault, args: argparse.Namespace
) -> None:
    key = getpass.getpass(f"Paste the {args.provider} API key (input hidden): ").strip()
    if len(key) < 20:
        sys.exit("That does not look like an API key")
    for old in (
        await session.scalars(
            select(ProviderAccount).where(
                ProviderAccount.provider == args.provider, ProviderAccount.status == "active"
            )
        )
    ).all():
        old.status = "retired"
    session.add(
        ProviderAccount(
            provider=args.provider,
            key_enc=vault.encrypt(key, PROVIDER_KEY_PURPOSE.format(provider=args.provider)),
            key_last4=last4(key),
        )
    )
    session.add(
        AuditEvent(
            actor=_actor(),
            action="provider_key.add",
            target=f"provider:{args.provider}",
            details={"last4": last4(key)},
        )
    )
    await session.commit()
    print(f"Key ending in {last4(key)} saved for {args.provider}. Restart the API to load it.")


async def seed_models_cmd(session: AsyncSession, _vault: Vault, _args: argparse.Namespace) -> None:
    for provider, model, inp, out, cached in SEED_MODELS:
        row = await session.scalar(
            select(AIModel).where(AIModel.provider == provider, AIModel.model_id == model)
        )
        if row is None:
            row = AIModel(provider=provider, model_id=model)
            session.add(row)
        row.input_price, row.output_price = Decimal(inp), Decimal(out)
        row.cached_input_price = Decimal(cached)
        row.cache_write_price = (Decimal(inp) * Decimal("1.25")).quantize(Decimal("0.00000001"))
        row.enabled = True
    session.add(
        AuditEvent(
            actor=_actor(),
            action="models.seed",
            target="models",
            details={"count": len(SEED_MODELS)},
        )
    )
    await session.commit()
    print(f"Seeded {len(SEED_MODELS)} models.")


COMMANDS = {
    "create-app": create_app_cmd,
    "rotate-app-secret": rotate_secret_cmd,
    "retire-old-secret": retire_secret_cmd,
    "add-provider-key": add_provider_key_cmd,
    "seed-models": seed_models_cmd,
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-app")
    p.add_argument("slug")
    p.add_argument("--name", required=True)
    p.add_argument("--tasks", required=True, help="Comma-separated task names")
    p.add_argument("--callback-url", default=None)
    for name in ("rotate-app-secret", "retire-old-secret"):
        sub.add_parser(name).add_argument("slug")
    sub.add_parser("add-provider-key").add_argument(
        "provider", choices=["anthropic", "openai", "gemini", "xai"]
    )
    sub.add_parser("seed-models")
    return parser.parse_args(argv)


async def main(argv: list[str]) -> None:
    args = parse_args(argv)
    settings = get_settings()
    vault = Vault({settings.master_key_id: settings.master_key_bytes()}, settings.master_key_id)
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            await COMMANDS[args.command](session, vault, args)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
