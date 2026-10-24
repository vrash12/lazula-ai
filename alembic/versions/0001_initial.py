"""Initial M1 schema: apps, tenants, house styles, provider keys, models, requests, ledger, audit.

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 8)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("secret_next_enc", sa.Text(), nullable=True),
        sa.Column("callback_url", sa.String(500), nullable=True),
        sa.Column("allowed_tasks", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False),
        sa.Column("external_ref", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("app_id", "external_ref", name="uq_tenant_app_ref"),
    )
    op.create_index("ix_tenants_app_id", "tenants", ["app_id"])
    op.create_table(
        "house_styles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("always_tags", sa.JSON(), nullable=False),
        sa.Column("avoid_words", sa.JSON(), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "provider_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("key_enc", sa.Text(), nullable=False),
        sa.Column("key_last4", sa.String(4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_provider_accounts_provider", "provider_accounts", ["provider"])
    op.create_table(
        "models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("input_price", MONEY, nullable=False),
        sa.Column("output_price", MONEY, nullable=False),
        sa.Column("cached_input_price", MONEY, nullable=False),
        sa.Column("cache_write_price", MONEY, nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("provider", "model_id", name="uq_model_provider_id"),
    )
    op.create_table(
        "requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(40), nullable=False, unique=True),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("app_id", sa.Integer(), sa.ForeignKey("apps.id"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("task_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("model", sa.String(140), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("body_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("app_id", "request_id", name="uq_request_app_rid"),
    )
    op.create_index("ix_requests_tenant_id", "requests", ["tenant_id"])
    op.create_index("ix_requests_created_at", "requests", ["created_at"])
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_record_id", sa.Integer(), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", MONEY, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("request_record_id", "attempt", name="uq_usage_attempt"),
    )
    op.create_index("ix_usage_ledger_tenant_id", "usage_ledger", ["tenant_id"])
    op.create_index("ix_usage_ledger_created_at", "usage_ledger", ["created_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target", sa.String(200), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    for table in (
        "audit_events",
        "usage_ledger",
        "requests",
        "models",
        "provider_accounts",
        "house_styles",
        "tenants",
        "apps",
    ):
        op.drop_table(table)
