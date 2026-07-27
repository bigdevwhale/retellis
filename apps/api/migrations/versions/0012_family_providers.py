"""family_providers — the family's shared BYOK key (encrypted to the family passphrase)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-09 00:00:00

A parallel of the ``providers`` table keyed by ``family_id`` instead of
``user_id``. The family owner's API key is sealed in the family vault with a
family passphrase the owner shares with members out-of-band. Members' browsers
derive the family master key with Argon2id(family_passphrase, family_salt) and
unlock the family provider's ``enc_blob`` the same way the personal vault
unlocks a personal provider. The server cannot decrypt it — same zero-knowledge
contract as the personal vault.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "family_providers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("family_id", sa.String(64), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("key_handle", sa.String(64), nullable=True),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("enc_blob", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_family_providers_family_created",
        "family_providers",
        ["family_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_family_providers_family_created", table_name="family_providers")
    op.drop_table("family_providers")
