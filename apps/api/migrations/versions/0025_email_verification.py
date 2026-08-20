"""add users.email_verified + users.email_verified_at

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-20 00:00:00

Email ownership for local-account signup. ``email_verified`` defaults to True
(server_default true) so **existing** users — registered before the
verification feature — are backfilled as already-trusted; new local-signup rows
created under FEATURE_EMAIL_VERIFICATION insert False explicitly via
``create_user(..., email_verified=False)``, so the server_default only covers the
backfill, not new rows. ``email_verified_at`` is NULL until the user clicks the
verification link (``set_email_verified`` stamps it).

No key material in these columns — a boolean + a timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default true backfills existing rows as trusted. New rows specify
    # the value explicitly in create_user, so this default is only the backfill.
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")