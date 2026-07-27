"""families — owner-customisable therapist prompt for the ``fam`` persona

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-10 00:00:00

Adds three columns to the ``families`` table so the family owner can persist
their own customisation of the family therapist's system prompt. The body
lives on the family row as plaintext (the prompt is shared owner-authored
content, not a key — it is subject to the same disclosure rules as the
custom-persona prompt, but it is NOT zero-knowledge like the family BYOK
key). Audit fields track the last setter; the schema intentionally has no
history table — the last writer + timestamp is the audit.

The companion behavior wired by this migration:
  - ``therapist_prompt``: the owner-composed prompt body, or NULL to fall
    back to the static ``fam`` builtin (mirrored on the client).
  - ``therapist_prompt_set_by``: user_id of the last setter (denormalised
    ``set_by_display_name`` is computed in the router via auth_store).
  - ``therapist_prompt_set_at``: UTC timestamp of the last save.

All three columns are nullable; the upgrade is additive and back-compat with
existing families (NULL = "no customisation" = the static builtin is used).
No new index — the columns are 1:1 with the family PK, only ever read
together with the family row, and the only writer is the owner (low QPS).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "families",
        sa.Column("therapist_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "families",
        sa.Column("therapist_prompt_set_by", sa.String(64), nullable=True),
    )
    op.add_column(
        "families",
        sa.Column(
            "therapist_prompt_set_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Reverse order: drop the audit fields first, then the body.
    op.drop_column("families", "therapist_prompt_set_at")
    op.drop_column("families", "therapist_prompt_set_by")
    op.drop_column("families", "therapist_prompt")
