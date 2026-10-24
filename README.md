# LAZULA.ai

Version: 0.3 | Prepared: October 24, 2026 | Stage: M1 in progress (hub foundation code exists)

LAZULA.ai is the shared AI service for every LAZULA app. It has two parts:

- **The hub:** one signed task API that SermonPost, the LAZULA Media Team app, the LAZULA Real Estate Solution and future apps call instead of calling AI providers directly. The hub chooses the model, falls back when a provider fails, validates every answer, applies safety rules and records cost.
- **LAZULA Studio:** an AI document engine on top of the hub. It turns facts and a brand kit into checked, print-ready documents (pamphlets, listing brochures, flyers, carousels) for human approval.

Python 3.12, FastAPI, MySQL 8, Redis, Celery and Playwright. The existing PHP apps stay in PHP and connect over HTTPS.

## Status

**M1 (hub foundation) is in progress.** Built and tested locally: signed task API, tenants and house styles, key vault, Claude adapter, fallback engine, cost ledger, idempotency, two tasks (`sermon.captions`, `post.platform_versions`), operator CLI, Docker and CI files, and the PHP connector. 57 tests pass. Not yet done: async tasks and webhooks, a second provider adapter, MySQL integration tests, a Docker build and a staging deploy.

## Quick start

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/). Tests need nothing else (they use in-memory SQLite and a fake Redis, and never call real AI providers).

```bash
uv sync
```

```bash
uv run pytest
```

To run the API locally you also need MySQL and Redis (Docker is easiest):

```bash
docker compose -f docker/compose.dev.yml up -d
```

```bash
cp .env.example .env
```

```bash
uv run alembic upgrade head
```

```bash
uv run python -m app.cli seed-models
```

```bash
uv run python -m app.cli create-app sermonpost --name SermonPost --tasks sermon.captions,post.platform_versions
```

Add `ENABLE_FAKE_PROVIDER=true` to `.env` to try tasks without an AI key, or add a real key with `uv run python -m app.cli add-provider-key anthropic`. Then:

```bash
uv run uvicorn --factory app.main:app_factory --reload
```

API docs: `http://127.0.0.1:8000/docs`. Every request must be signed with HMAC-SHA256; `connectors/php/LazulaAiClient.php` shows how.

## Related LAZULA projects

| Project | Language | Relationship |
|---|---|---|
| SermonPost | PHP + MySQL | First app to move onto the hub (M3) |
| LAZULA Media Team app | PHP + MySQL | Second app (M4); also hosts Studio's human review flow |
| LAZULA Real Estate Solution | Laravel 13 + Vue 3 | Builds its AI features on the hub (M5) |
