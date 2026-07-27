"""add families.use_owner_personal_key

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-24 00:00:00

Per-family owner-only flag: when on, family chat turns resolve the BYOK key
from the owner's personal ``providers`` row (envelope ciphertext under
``MESSENGER_TOKEN_DEK``) instead of ``family_providers``. Lets the owner share
their existing personal key with the family without re-entering it.

Mutually exclusive with family keys in the UI (toggle on hides the family key
form); server-side, when the flag is on the personal-provider lookup wins and
``family_providers`` is not consulted for the turn. Security model is unchanged
— the server holds the DEK and can decrypt the owner's key in memory for a
member's turn, zeroized after (same honest disclosure as family keys). The
owner is resolved from the family record (``fam.owner_user_id``), never from a
client-supplied value, so a member cannot retarget the lookup. No key material
is stored in this column — it is a boolean.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "families",
        sa.Column("use_owner_personal_key", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("families", "use_owner_personal_key")