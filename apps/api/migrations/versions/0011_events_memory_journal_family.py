"""events/memories/journal/usage get family_id + visibility + participant_user_id

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-09 00:00:00

Wires the family-scope recall model onto the per-user tables. ``family_id``
is the family the row belongs to (NULL for non-family rows — back-compat with
existing single-user data). ``visibility`` is "private" (only the
``participant_user_id`` member can recall in their own 1:1) or "shared"
(recallable by all family members in both 1:1 and joint sessions; this is
the ONLY scope the joint session reads from). ``participant_user_id`` is the
speaker for user-role rows in a private scope (NULL for assistant rows and
for shared rows where the assistant renders the same to every member).

The composite index ``(family_id, visibility, participant_user_id)`` serves
both recall predicates:
  - solo-M:   family_id == F AND (visibility == "shared" OR (visibility == "private" AND participant_user_id == M))
  - joint:    family_id == F AND visibility == "shared"
The per-user indexes still serve non-family traffic unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_scope_columns(table: str) -> None:
    op.add_column(table, sa.Column("family_id", sa.String(64), nullable=True))
    op.add_column(
        table, sa.Column("visibility", sa.String(16), nullable=False, server_default="private")
    )
    op.add_column(table, sa.Column("participant_user_id", sa.String(64), nullable=True))
    op.create_index(
        f"ix_{table}_family_visibility_participant",
        table,
        ["family_id", "visibility", "participant_user_id"],
    )


def upgrade() -> None:
    _add_scope_columns("events")
    _add_scope_columns("memories")
    _add_scope_columns("journal_entries")

    op.add_column("usage", sa.Column("family_id", sa.String(64), nullable=True))
    op.create_index("ix_usage_family_created", "usage", ["family_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_family_created", table_name="usage")
    op.drop_column("usage", "family_id")

    for table in ("journal_entries", "memories", "events"):
        op.drop_index(f"ix_{table}_family_visibility_participant", table_name=table)
        op.drop_column(table, "participant_user_id")
        op.drop_column(table, "visibility")
        op.drop_column(table, "family_id")
