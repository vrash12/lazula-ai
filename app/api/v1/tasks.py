"""Task endpoints (API_SPEC.md §2, ARCHITECTURE.md §4.1).

M1 runs tasks synchronously. Async tasks with webhooks are the remaining M1 work.
"""

from __future__ import annotations

import time
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import SessionDep, SignedDep
from app.api.schemas import TaskRequest, TaskResult, Usage
from app.core import idempotency
from app.core.engine import run_task
from app.core.errors import EngineFailure, InvalidInput, NotFound, ReplayedRequest, TaskNotAllowed
from app.core.ids import new_task_id, utcnow
from app.core.ledger import record_attempts
from app.core.logging import get_logger
from app.core.tenancy import house_style_for, require_active_tenant
from app.models import RequestRecord, UsageEntry
from app.tasks import get_task

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])
log = get_logger()


def describe_validation_error(exc: ValidationError) -> str:
    """Field paths and error types only; never the submitted values (no content in errors)."""
    parts = [
        f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['type']}" for e in exc.errors()[:10]
    ]
    return "Invalid input: " + "; ".join(parts)


async def _ledger_offset(session: SessionDep, record: RequestRecord) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(UsageEntry)
        .where(UsageEntry.request_record_id == record.id)
    )
    return int(count or 0)


@router.post("", response_model=TaskResult)
async def create_task(
    body: TaskRequest, signed: SignedDep, session: SessionDep, request: Request
) -> JSONResponse:
    state = request.app.state
    app = signed.app

    task = get_task(body.task)
    if task is None or body.task not in app.allowed_tasks:
        raise TaskNotAllowed()
    if body.options.run_async:
        raise InvalidInput("Async tasks are not available yet; send options.async = false")
    try:
        task_input = task.input_model.model_validate(body.input)
    except ValidationError as exc:
        raise InvalidInput(describe_validation_error(exc)) from None
    tenant = await require_active_tenant(session, app.id, body.tenant)

    cached = await idempotency.begin(
        state.redis, app.id, signed.request_id, signed.digest, running_ttl=int(task.timeout_s) + 60
    )
    if cached is not None:
        return JSONResponse(cached.body, status_code=cached.status_code)

    try:
        record = await session.scalar(
            select(RequestRecord).where(
                RequestRecord.app_id == app.id, RequestRecord.request_id == signed.request_id
            )
        )
        if record is not None and (
            record.body_digest != signed.digest or record.status == "completed"
        ):
            # Different body, or results already expired from the idempotency cache.
            raise ReplayedRequest()
        if record is None:
            record = RequestRecord(
                public_id=new_task_id(),
                request_id=signed.request_id,
                app_id=app.id,
                tenant_id=tenant.id,
                task=task.name,
                task_version=task.version,
                status="running",
                body_digest=signed.digest,
            )
            session.add(record)
            await session.flush()
        else:  # a retry of a request that failed earlier
            record.status, record.error_code = "running", None
        offset = await _ledger_offset(session, record)

        started = time.monotonic()
        try:
            result = await run_task(
                task,
                task_input,
                quality=body.options.quality,
                house_style=await house_style_for(session, tenant),
                registry=state.providers,
                enable_fake=state.settings.enable_fake_provider,
            )
        except EngineFailure as failure:
            await record_attempts(
                session,
                record,
                [(a.ref.provider, a.ref.model, a.outcome, a.usage) for a in failure.attempts],
                start=offset,
            )
            record.status, record.error_code = "failed", str(failure.code)
            record.attempts = offset + len(failure.attempts)
            record.latency_ms = int((time.monotonic() - started) * 1000)
            record.completed_at = utcnow()
            await session.commit()
            log.warning(
                "task_failed",
                task=task.name,
                code=str(failure.code),
                attempts=len(failure.attempts),
            )
            raise

        attempts = [(a.ref.provider, a.ref.model, a.outcome, a.usage) for a in result.attempts]
        cost = await record_attempts(session, record, attempts, start=offset)
        record.status = "completed"
        record.model = str(result.ref)
        record.fallback_used = result.fallback_used
        record.attempts = offset + len(result.attempts)
        record.latency_ms = int((time.monotonic() - started) * 1000)
        record.completed_at = utcnow()
        await session.commit()
    except BaseException:
        await idempotency.release(state.redis, app.id, signed.request_id)
        raise

    usage = Usage(
        input_tokens=sum(a.usage.input_tokens for a in result.attempts),
        cached_input_tokens=sum(a.usage.cached_input_tokens for a in result.attempts),
        output_tokens=sum(a.usage.output_tokens for a in result.attempts),
        cost_usd=str(cost.quantize(Decimal("0.0001"))),
    )
    payload = TaskResult(
        id=record.public_id,
        status="completed",
        task=task.name,
        task_version=task.version,
        model=str(result.ref),
        fallback_used=result.fallback_used,
        output=result.output.model_dump(mode="json"),
        usage=usage,
    ).model_dump(mode="json")
    await idempotency.complete(
        state.redis,
        app.id,
        signed.request_id,
        signed.digest,
        200,
        payload,
        ttl=state.settings.result_cache_ttl_seconds,
    )
    log.info(
        "task_completed",
        task=task.name,
        model=str(result.ref),
        attempts=len(result.attempts),
        latency_ms=record.latency_ms,
        cost_usd=usage.cost_usd,
    )
    return JSONResponse(payload)


@router.get("/{task_id}", response_model=TaskResult)
async def get_task_status(task_id: str, signed: SignedDep, session: SessionDep) -> TaskResult:
    record = await session.scalar(
        select(RequestRecord).where(
            RequestRecord.public_id == task_id, RequestRecord.app_id == signed.app.id
        )
    )
    if record is None:
        raise NotFound("Unknown task ID for this app")
    status = record.status if record.status in ("running", "completed", "failed") else "failed"
    return TaskResult(
        id=record.public_id,
        status=status,
        task=record.task,
        task_version=record.task_version,
        model=record.model,
        fallback_used=record.fallback_used,
    )
