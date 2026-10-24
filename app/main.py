"""FastAPI application factory."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis_asyncio
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app import __version__
from app.api import health
from app.api.v1 import tasks, tenants
from app.core.crypto import Vault
from app.core.errors import ErrorCode, LazulaError
from app.core.logging import configure_logging, get_logger
from app.core.signing import REQUEST_ID_PATTERN
from app.db import make_engine, make_sessionmaker
from app.providers.registry import ProviderRegistry, build_registry
from app.settings import Settings, get_settings

log = get_logger()


def _error_body(code: str, message: str, request: Request) -> dict[str, Any]:
    rid = request.headers.get("x-lazula-request-id", "")
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": rid if REQUEST_ID_PATTERN.match(rid) else None,
        }
    }


async def init_state(
    app: FastAPI,
    settings: Settings,
    *,
    redis_client: Any | None = None,
    providers: ProviderRegistry | None = None,
) -> None:
    """Create shared resources. Tests call this directly with fakes."""
    engine = make_engine(settings.database_url)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.vault = Vault(
        {settings.master_key_id: settings.master_key_bytes()}, settings.master_key_id
    )
    app.state.redis = redis_client or redis_asyncio.from_url(
        settings.redis_url, decode_responses=True
    )
    if providers is None:
        async with app.state.sessionmaker() as session:
            providers = await build_registry(
                session, app.state.vault, enable_fake=settings.enable_fake_provider
            )
    app.state.providers = providers


async def close_state(app: FastAPI) -> None:
    await app.state.engine.dispose()
    await app.state.redis.aclose()


class RequestLogMiddleware(BaseHTTPMiddleware):
    """One metadata-only log line per request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        structlog.contextvars.clear_contextvars()
        started = time.monotonic()
        response = await call_next(request)
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=int((time.monotonic() - started) * 1000),
            app=getattr(request.state, "app_slug", None),
            request_id=getattr(request.state, "request_id", None),
        )
        return response


def create_app(settings: Settings | None = None, *, manage_state: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if manage_state:
            await init_state(app, settings)
        yield
        if manage_state:
            await close_state(app)

    docs = settings.lazula_env != "production"
    app = FastAPI(
        title="LAZULA.ai",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(RequestLogMiddleware)

    @app.exception_handler(LazulaError)
    async def lazula_error(request: Request, exc: LazulaError) -> JSONResponse:
        return JSONResponse(
            _error_body(str(exc.code), exc.message, request), status_code=exc.http_status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field paths and error types only; never echo submitted values.
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()[:10]]
        message = "Invalid input: " + "; ".join(parts)
        return JSONResponse(_error_body(ErrorCode.INVALID_INPUT, message, request), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__, path=request.url.path)
        return JSONResponse(
            _error_body(ErrorCode.INTERNAL_ERROR, "Something went wrong", request), status_code=500
        )

    app.include_router(health.router)
    app.include_router(tasks.router)
    app.include_router(tenants.router)
    return app


def app_factory() -> FastAPI:
    """Server entry point.

    Gunicorn: `app.main:app_factory()`; Uvicorn: `--factory app.main:app_factory`.
    """
    return create_app()
