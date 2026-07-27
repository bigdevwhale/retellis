"""add providers.api_key_ciphertext + family_providers.api_key_ciphertext

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-23 00:00:00

Server-side envelope-encrypted BYOK API key storage. The plaintext key is
ECDH-sealed by the client once at onboarding (to the server session pubkey),
decrypted in memory, re-wrapped with the ``MESSENGER_TOKEN_DEK`` envelope
(``crypto/envelope.py`` ``EnvelopeCipher`` — XSalsa20-Poly1305), and stored as
base64 ``nonce||ct`` in ``api_key_ciphertext``. Per turn the server envelope-
decrypts it, builds a ``DecryptedKey``, and zeroizes after the call — same
honest-zeroize disclosure as the existing per-turn blob path.

This is a deliberate security-model change: the server CAN now decrypt BYOK
keys (it holds the DEK). It is envelope encryption against DB-dump exposure,
NOT zero-knowledge. The old ``enc_blob`` / ``family_enc_blob_seed`` columns
are kept (no longer populated; harmless dead columns) to avoid contract churn
— dropping them is a separate cleanup migration.

Honest limits: the envelope stores the full key JSON payload
(``{provider_kind, api_key, base_url, extra}``) so Bedrock-style extras survive
the round-trip. The plaintext key is never logged; ``grep -r 'sk-' deploy/``
stays empty (the column is base64 ciphertext).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("api_key_ciphertext", sa.Text, nullable=True))
    op.add_column("family_providers", sa.Column("api_key_ciphertext", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("family_providers", "api_key_ciphertext")
    op.drop_column("providers", "api_key_ciphertext")