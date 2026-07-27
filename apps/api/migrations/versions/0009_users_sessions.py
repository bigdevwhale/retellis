"""add users + sessions tables (auth & deployment-mode layer)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09 00:00:00

Authenticated accounts + opaque session rows backing the session cookie. The
``users`` table links an identity by ``(issuer, subject)`` (OIDC sub / local email
/ trusted-header value) and carries entitlements (``plan``, ``credits_usd``);
``password_hash`` (Argon2id) is set only for the local backend. The vault
passphrase is NOT stored here — it never leaves the browser, and the server
cannot decrypt ``providers.enc_blob`` (migration 0007). ``sessions`` is revocable
(``revoked_at``) and expiring (``expires_at``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("display_name", sa.String(160), nullable=True),
        sa.Column("plan", sa.String(40), nullable=False, server_default="self_hosted_free"),
        sa.Column("credits_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )
    # Partial unique index on email so multiple NULL emails (OIDC without email)
    # don't collide, while still preventing duplicate accounts per address.
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    op.create_table(
        "sessions",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_user", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("uq_users_email", table_name="users")
    op.drop_table("users")
