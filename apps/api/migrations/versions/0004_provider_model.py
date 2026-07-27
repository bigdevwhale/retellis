"""add providers.model (user-selected model id, nullable)

Revision ID: 0004
Revises: 0003
Create Date: 2025-01-04 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing rows keep NULL (use the server default for the kind).
    op.add_column("providers", sa.Column("model", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("providers", "model")
