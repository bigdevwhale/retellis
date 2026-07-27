"""add memory_shares table (cross-persona live memory link — a reference, not a copy)

Revision ID: 0006
Revises: 0005
Create Date: 2025-01-06 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_shares",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("donor_persona_id", sa.String(64), nullable=False, index=True),
        sa.Column("receiver_persona_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id",
            "receiver_persona_id",
            "donor_persona_id",
            name="uq_memory_shares_triple",
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_shares")
