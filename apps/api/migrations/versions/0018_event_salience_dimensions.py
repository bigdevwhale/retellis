"""add short_term_salience and emotional_intensity to events

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-17 00:00:00

Multi-dimensional salience for long-horizon conversations:
- salience: long-term recall weight (existing column, semantic meaning unchanged)
- short_term_salience: boost for the immediate next turns, decays quickly
- emotional_intensity: acute emotional charge that should affect tone and recall
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("short_term_salience", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("events", sa.Column("emotional_intensity", sa.Float(), nullable=False, server_default="0.0"))


def downgrade() -> None:
    op.drop_column("events", "emotional_intensity")
    op.drop_column("events", "short_term_salience")
