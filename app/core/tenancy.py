"""Tenant-scoped data access (INV-04).

Every lookup of tenant-owned data goes through these functions, which always
filter by the calling app. Never query tenant tables by external reference
alone.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TaskNotAllowed, TenantNotFound
from app.models import HouseStyle, Tenant
from app.tasks.base import HouseStyleData


async def find_tenant(session: AsyncSession, app_id: int, external_ref: str) -> Tenant | None:
    tenant: Tenant | None = await session.scalar(
        select(Tenant).where(Tenant.app_id == app_id, Tenant.external_ref == external_ref)
    )
    return tenant


async def require_active_tenant(session: AsyncSession, app_id: int, external_ref: str) -> Tenant:
    tenant = await find_tenant(session, app_id, external_ref)
    if tenant is None:
        raise TenantNotFound()
    if tenant.status != "active":
        raise TaskNotAllowed("This tenant is suspended")
    return tenant


async def house_style_for(session: AsyncSession, tenant: Tenant) -> HouseStyleData:
    style = await session.scalar(select(HouseStyle).where(HouseStyle.tenant_id == tenant.id))
    if style is None:
        return HouseStyleData()
    return HouseStyleData(
        rules=list(style.rules),
        always_tags=list(style.always_tags),
        avoid_words=list(style.avoid_words),
    )
