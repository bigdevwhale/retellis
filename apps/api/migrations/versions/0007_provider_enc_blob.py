"""add providers.enc_blob (zero-knowledge at-rest key backup)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-07 00:00:00

The server stores an encrypted copy of the API key so a browser cache wipe can
be recovered with just the passphrase. The column holds base64 of
``salt || nonce || XChaCha20-Poly1305 ciphertext`` keyed by
Argon2id(passphrase, salt) — opaque ciphertext the server cannot decrypt (the
passphrase never leaves the browser). Nullable: existing rows keep NULL (no
sync backup) and continue to work via the per-request ECDH key flow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing rows keep NULL (no at-rest backup until the client
    # re-onboards / re-uploads). The plaintext key is never stored — only
    # ciphertext the server can't read.
    op.add_column("providers", sa.Column("enc_blob", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("providers", "enc_blob")
