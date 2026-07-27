"""events with pgvector embedding

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-02 00:00:00

Requires the ``vector`` extension (created at first-boot by
deploy/postgres/init.sql). Uses exact cosine distance (``<=>``); ivfflat index
deferred until >50k events (PLAN §2).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBED_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("persona_id", sa.String(64), nullable=False, index=True),
        sa.Column("convo_id", sa.String(64), nullable=False, index=True),
        sa.Column("prev_event_id", sa.String(64), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("salience", sa.Float(), nullable=False, server_default="0"),
        sa.Column("emotion_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_events_persona_created", "events", ["persona_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_events_persona_created", table_name="events")
    op.drop_table("events")
