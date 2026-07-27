"""add billing tables (plans, subscriptions, invoices, webhooks, profiles)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-16 00:00:00

Hosted-only subscription billing. The purchase is a redirect to the provider's
hosted checkout (Paddle for WW, ЮKassa for RU); webhooks are the single source
of truth for subscription state. A successful payment calls ``set_user_plan``
(atomic UPDATE of ``users.plan`` + ``credits_usd += grant``) — the existing
``out_of_credits`` gate and per-turn ``decrement_credits`` are unchanged.

Prices are minor units (cents for USD, kopecks for RUB) — never float.
``credits_grant_usd`` is USD-denominated regardless of payment currency. The
seeded plans (plus/pro × WW/RU) reflect the locked product decisions: plus→$10
credits, pro→$25 credits; Paddle 7-day trial (WW only — РФ has no trial due to
54-ФЗ); RUB price list for RU (690₽/1390₽). ``provider_price_id`` is left NULL
until each plan is linked to a provider product in the dashboard.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("slug", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("geo", sa.String(4), nullable=False),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credits_grant_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("provider_price_id", sa.String(120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("plan_slug", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_sub_id", sa.String(120), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="trialing"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_country", sa.String(2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "provider", "provider_sub_id", name="uq_subscriptions_provider_sub"
        ),
    )
    op.create_index("ix_subscriptions_user", "subscriptions", ["user_id"])

    op.create_table(
        "billing_invoices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("subscription_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_invoice_id", sa.String(120), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_url", sa.Text(), nullable=True),
        sa.Column("fiscal_receipt_id", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_billing_invoices_sub", "billing_invoices", ["subscription_id"])
    op.create_index("ix_billing_invoices_user", "billing_invoices", ["user_id"])

    op.create_table(
        "billing_webhook_events",
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("provider_event_id", sa.String(160), primary_key=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "billing_profiles",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("provider", sa.String(16), nullable=True),
        sa.Column("provider_customer_id", sa.String(120), nullable=True),
        sa.Column("default_payment_method_token", sa.String(160), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", name="uq_billing_profiles_user"),
    )

    # Seed the plan catalogue. Idempotent so re-running (or a future migration
    # that re-seeds) doesn't collide. Provider price ids are linked later from
    # the provider dashboard; NULL here means checkout 503s until linked.
    op.execute(
        sa.text(
            """
            INSERT INTO billing_plans (slug, name, price_cents, currency, interval, geo, trial_days, credits_grant_usd, provider_price_id, active)
            VALUES
              ('plus_ww',  'Plus',  1200,  'USD', 'month', 'WW', 7, 10.0, NULL, true),
              ('pro_ww',   'Pro',   2400,  'USD', 'month', 'WW', 7, 25.0, NULL, true),
              ('plus_ru',  'Plus',  69000, 'RUB', 'month', 'RU', 0, 10.0, NULL, true),
              ('pro_ru',   'Pro',   139000,'RUB', 'month', 'RU', 0, 25.0, NULL, true)
            ON CONFLICT (slug) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_table("billing_profiles")
    op.drop_table("billing_webhook_events")
    op.drop_index("ix_billing_invoices_user", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_sub", table_name="billing_invoices")
    op.drop_table("billing_invoices")
    op.drop_index("ix_subscriptions_user", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("billing_plans")
