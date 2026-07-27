"""add family_providers.embeddings_model (family semantic memory)

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-17 00:00:00

Same contract as providers.embeddings_model (migration 0019): a model id,
never a key — the recall embedding call reuses the family turn's ECDH-sealed
key. NULL = family semantic memory off.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("family_providers", sa.Column("embeddings_model", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("family_providers", "embeddings_model")
