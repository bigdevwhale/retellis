"""families + members + invites + family_id/family_role on users

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-09 00:00:00

Adds the multi-member family surface. Each user can be in AT MOST ONE family
(single ``users.family_id`` value, NULL when not in a family). The family
itself is a small row carrying owner + name + zero-knowledge family-vault
metadata (``family_salt`` for Argon2id, ``family_enc_blob_seed`` as a sentinel
sealed envelope so a member's browser can tell whether the family vault is
initialized). ``family_members`` is a per-user membership card (composite PK
on ``(family_id, user_id)``) carrying the labels the family therapist sees in
the prompt (``family_display_name``, ``relation``, ``color``) and the role
(``owner`` is materialized on the family row too for fast lookups).
``family_invites`` is a pending-invite queue; the actual token lives only in
the email link (sealed) — the DB stores it hashed for cheap revocation +
replay-resistance.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "families",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("owner_user_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # Family vault metadata. Both columns are zero-knowledge ciphertext /
        # opaque salt — the server cannot decrypt ``family_enc_blob_seed``. See
        # apps/web/lib/vault.ts for the family vault module.
        sa.Column("family_salt", sa.String(64), nullable=True),
        sa.Column("family_enc_blob_seed", sa.Text(), nullable=True),
    )

    op.create_table(
        "family_members",
        sa.Column("family_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("family_role", sa.String(16), nullable=False),
        sa.Column("family_display_name", sa.String(160), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_family_members_family", "family_members", ["family_id"])
    op.create_index("ix_family_members_user", "family_members", ["user_id"])

    op.create_table(
        "family_invites",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("family_id", sa.String(64), nullable=False, index=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_family_invites_family_email", "family_invites", ["family_id", "email"])

    # Add family scope to users. Each user belongs to AT MOST one family.
    op.add_column("users", sa.Column("family_id", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("family_role", sa.String(16), nullable=True))
    op.create_index("ix_users_family", "users", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_users_family", table_name="users")
    op.drop_column("users", "family_role")
    op.drop_column("users", "family_id")
    op.drop_index("ix_family_invites_family_email", table_name="family_invites")
    op.drop_table("family_invites")
    op.drop_index("ix_family_members_user", table_name="family_members")
    op.drop_index("ix_family_members_family", table_name="family_members")
    op.drop_table("family_members")
    op.drop_table("families")
