"""Unsigned health endpoints. They reveal no details (SECURITY.md §4.5)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.logging import get_logger

router = APIRouter()
log = get_logger()


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    try:
        async with state.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        await state.redis.ping()
        if not state.providers.names():
            raise RuntimeError("no providers configured")
    except Exception as exc:  # report not-ready for any dependency failure
        log.warning("not_ready", reason=type(exc).__name__)
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready"})
