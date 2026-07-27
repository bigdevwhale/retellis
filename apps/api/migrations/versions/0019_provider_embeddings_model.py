"""add providers.embeddings_model (BYOK semantic memory)

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-17 00:00:00

User-selected embedding model for semantic memory recall (e.g.
"text-embedding-3-small"). NULL = semantic memory off for this provider —
recall stays on the zero-config hash embedder (or the server env embedder).
The embedding call reuses the same per-request ECDH-sealed BYOK key as the
chat call; no new key material is stored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("embeddings_model", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("providers", "embeddings_model")
