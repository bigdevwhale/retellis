"""events.embedding_model + HNSW ANN index (I11 — semantic recall at scale)

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-17 00:00:00

``embedding_model`` records WHICH embedder produced the row's vector so the
ANN recall path never compares across embedding spaces:
- NULL       — legacy feature-hashing vector (write-path default without a
               semantic embedder). Excluded from ANN candidate queries.
- model id   — semantic vector from that litellm model (env- or BYOK-supplied).

The HNSW index makes the ANN prefilter (`ORDER BY embedding <=> $vec LIMIT n`)
sub-linear once the events table outgrows the exact-scan comfort zone (~50k
rows). pgvector >= 0.5 (the pg16 pgvector image ships it). Building HNSW on an
existing small table is cheap; the index is cosine-ops to match the recall
metric.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("embedding_model", sa.String(80), nullable=True))
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_embedding_hnsw "
        "ON events USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_events_embedding_hnsw")
    op.drop_column("events", "embedding_model")
