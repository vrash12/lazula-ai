"""Tenant registration and house styles (API_SPEC.md §2, INTEGRATION_GUIDE.md §3.4).

Authentication runs before body validation: FastAPI resolves dependencies
(`SignedDep`) before it reports body errors, so unsigned callers learn nothing
about the expected input.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import SessionDep, SignedDep
from app.api.schemas import HouseStyleUpsert, TenantOut, TenantRef, TenantUpsert
from app.core.errors import TenantNotFound
from app.core.tenancy import find_tenant
from app.models import AuditEvent, HouseStyle, Tenant

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])


@router.put("/{tenant}", response_model=TenantOut)
async def upsert_tenant(
    tenant: TenantRef, body: TenantUpsert, signed: SignedDep, session: SessionDep
) -> TenantOut:
    row = await find_tenant(session, signed.app.id, tenant)
    action = "tenant.update"
    if row is None:
        row = Tenant(app_id=signed.app.id, external_ref=tenant, name=body.name, status=body.status)
        session.add(row)
        action = "tenant.create"
    else:
        row.name, row.status = body.name, body.status
    session.add(
        AuditEvent(
            actor=f"app:{signed.app.slug}",
            action=action,
            target=f"tenant:{signed.app.slug}/{tenant}",
            details={"status": body.status},
        )
    )
    await session.commit()
    return TenantOut(tenant=tenant, name=row.name, status=row.status)


@router.put("/{tenant}/house-style", status_code=204)
async def put_house_style(
    tenant: TenantRef, body: HouseStyleUpsert, signed: SignedDep, session: SessionDep
) -> Response:
    row = await find_tenant(session, signed.app.id, tenant)
    if row is None:
        raise TenantNotFound()
    style = await session.scalar(select(HouseStyle).where(HouseStyle.tenant_id == row.id))
    if style is None:
        style = HouseStyle(tenant_id=row.id)
        session.add(style)
    style.rules, style.always_tags, style.avoid_words = (
        body.rules,
        body.always_tags,
        body.avoid_words,
    )
    await session.commit()
    return Response(status_code=204)
