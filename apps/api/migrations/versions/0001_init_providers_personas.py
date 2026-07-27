"""init providers and personas

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("key_handle", sa.String(64), nullable=True),
    )
    op.create_table(
        "personas",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("tone", sa.JSON(), nullable=False),
        sa.Column("opening_line", sa.Text(), nullable=False),
        sa.Column("custom", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("personas")
    op.drop_table("providers")
