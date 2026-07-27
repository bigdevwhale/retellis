"""consumed_tokens — single-use token tracking (post-MVP hardening, PLAN §16 #2)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-09 00:00:00

Family invite tokens are sealed before being emailed; the server only stores
the SHA-256 hash. A naive "did this invite get accepted?" check via
``family_invites.accepted_at`` is racy: an attacker who replays the token
after deletion (or after accepted_at was set) could re-use it, and the
DELETE/expire paths race against the accept path. This table is the
authoritative replay defense — ``INSERT ... ON CONFLICT DO NOTHING`` with
the token_hash as the primary key. The first call returns the new row, every
replay is a no-op. The router calls ``consume_invite_token`` BEFORE looking
up the invite, so a replayed token 410s immediately regardless of the
invite's state (active, accepted, deleted, expired).

``kind`` is a forward-compatible slot: the same table backs future
single-use token families (magic-link, password-reset) without schema
changes — they just use a different ``kind`` value.

Zero knowledge is preserved: only token hashes land here. The plaintext
token never enters the DB.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consumed_tokens",
        sa.Column("token_hash", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # (kind, consumed_at) index lets future "list all consumed magic-links in
    # the last hour" admin queries run cheaply without a full scan.
    op.create_index(
        "ix_consumed_tokens_kind_consumed",
        "consumed_tokens",
        ["kind", "consumed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_consumed_tokens_kind_consumed", table_name="consumed_tokens")
    op.drop_table("consumed_tokens")
