"""create messengers table (per-user Telegram bots)

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-23 00:00:00

Per-user external-messenger link (Telegram first). The bot token is stored
ONLY as an XSalsa20-Poly1305 envelope ciphertext (NaCl ``SecretBox``, keyed by
``MESSENGER_TOKEN_DEK`` — see ``crypto/envelope.py``); the BYOK key (if the
user bound one) is stored the same way in ``byok_enc_blob``. Neither column
ever carries plaintext key material. ``next_offset`` persists the long-polling
cursor so a restart doesn't replay updates. Deleting the user cascades the
messenger row (same contract as providers/personas — migration 0016).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "messengers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_handshake"),
        # Fernet ciphertext of the bot token (never plaintext).
        sa.Column("bot_token_ciphertext", sa.Text, nullable=False),
        # Fernet ciphertext of the ECDH-decrypted BYOK key material, NULL when
        # the user bound the bot without BYOK (server-fallback keys).
        sa.Column("byok_enc_blob", sa.Text, nullable=True),
        sa.Column("persona_id", sa.String(64), nullable=False),
        # Telegram-side ids learned from updates (NULL until first update).
        sa.Column("chat_id", sa.BigInteger, nullable=True),
        sa.Column("bot_username", sa.String(64), nullable=True),
        sa.Column("bot_token_masked", sa.String(16), nullable=False),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_offset", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "kind", name="uq_messengers_user_kind"),
    )
    op.create_index(
        "ix_messengers_active",
        "messengers",
        ["status"],
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("ix_messengers_active", table_name="messengers")
    op.drop_table("messengers")
