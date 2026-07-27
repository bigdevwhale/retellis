"""add journal_entries table (user-authored diary; the /journal page)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-08 00:00:00

A first-class diary surface, separate from the chat ``events`` chain. Entries
are written directly by the user (or seeded from a chat message via "Save to
journal", which copies the message text and links ``source_convo_id`` /
``source_event_id``). ``mood`` + ``tags`` are authored by the user — the
journal surfaces them as-is and never generates affective claims ("disclose,
don't perform"). ``salience`` is the user's "matters to me" choice, not an
LLM-judged score. No pgvector, no FTS index in v1 (ILIKE over a moderate row
count is fine; revisit at scale).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("persona_id", sa.String(64), nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mood", sa.String(32), nullable=True),
        # JSONB so the tag facet can use @> containment server-side (correct
        # with pagination). Wire shape is still list[str].
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("salience", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("source_convo_id", sa.String(64), nullable=True),
        sa.Column("source_event_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Timeline scan: WHERE user_id = ? ORDER BY created_at DESC LIMIT ?.
    op.create_index("ix_journal_user_created", "journal_entries", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("journal_entries")
