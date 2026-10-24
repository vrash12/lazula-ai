from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import utcnow

Money = Numeric(18, 8)


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class App(Timestamps, Base):
    """A LAZULA app allowed to call the hub (SermonPost, Media Team, Real Estate)."""

    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    secret_next_enc: Mapped[str | None] = mapped_column(Text)
    callback_url: Mapped[str | None] = mapped_column(String(500))
    allowed_tasks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class Tenant(Timestamps, Base):
    """A church, client or team, owned by exactly one app."""

    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("app_id", "external_ref", name="uq_tenant_app_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True, nullable=False)
    external_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class HouseStyle(Timestamps, Base):
    __tablename__ = "house_styles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True, nullable=False)
    rules: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    always_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    avoid_words: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class ProviderAccount(Timestamps, Base):
    """An encrypted provider API key (never returned in plaintext)."""

    __tablename__ = "provider_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)


class AIModel(Timestamps, Base):
    """A provider model with prices per million tokens (USD)."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider", "model_id", name="uq_model_provider_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    input_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    output_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cached_input_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cache_write_price: Mapped[Decimal] = mapped_column(Money, nullable=False)


class RequestRecord(Base):
    """One app request. Metadata only: never content (INV-05)."""

    __tablename__ = "requests"
    __table_args__ = (UniqueConstraint("app_id", "request_id", name="uq_request_app_rid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    task_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str | None] = mapped_column(String(140))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(40))
    body_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class UsageEntry(Base):
    """One provider attempt with real token counts. Append-only."""

    __tablename__ = "usage_ledger"
    __table_args__ = (UniqueConstraint("request_record_id", "attempt", name="uq_usage_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_record_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Money, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditEvent(Base):
    """Admin and key changes. Append-only."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
