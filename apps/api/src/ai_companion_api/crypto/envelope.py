"""Envelope cipher for messenger bot tokens AND BYOK provider API keys.

Uses ``nacl.secret.SecretBox`` (XSalsa20-Poly1305) — the same primitive family
as the client vault, so no new dependency is introduced. Wire format is
``nonce (24 bytes) || ciphertext``; the store keeps it as base64 text in the
``bot_token_ciphertext`` / ``byok_enc_blob`` (messenger) and
``providers.api_key_ciphertext`` / ``family_providers.api_key_ciphertext``
(BYOK provider keys, migration 0023) columns.

Key management (honest, NOT zero-knowledge):

- ``MESSENGER_TOKEN_DEK`` env carries the base64-encoded 32-byte data
  encryption key. The server CAN decrypt what it stores — this is envelope
  encryption against DB-dump exposure, not a zero-knowledge scheme. The SAME DEK
  protects messenger bot tokens AND BYOK provider API keys (personal + family);
  they share the same threat model and lifecycle, so a second DEK + boot-
  validation path would add surface without adding isolation. BYOK keys now
  depend on this DEK being configured in hosted mode (the provider create
  endpoints 503 when it is missing, mirroring the Telegram bot-token endpoints).
- ``DEPLOYMENT_MODE=hosted`` with no key configured is a **hard boot failure**
  (silently losing every connected bot on restart is worse than refusing to
  start).
- Self-hosted with no key gets an **ephemeral** key plus a loud warning: all
  connected bots need re-binding after a restart. Keeps the zero-config DX.
"""

from __future__ import annotations

import base64
import binascii
import logging

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random

from ..config import Settings

logger = logging.getLogger(__name__)

_NONCE_LEN = SecretBox.NONCE_SIZE  # 24


class EnvelopeDecryptError(Exception):
    """Ciphertext failed to open (tampered, wrong key, or key rotated away)."""


class EnvelopeCipher:
    """Symmetric encrypt/decrypt for short secrets stored at rest."""

    def __init__(self, key: bytes) -> None:
        if len(key) != SecretBox.KEY_SIZE:
            raise ValueError(f"envelope key must be {SecretBox.KEY_SIZE} bytes")
        self._box = SecretBox(key)

    @classmethod
    def from_base64(cls, key_b64: str) -> EnvelopeCipher:
        try:
            key = base64.b64decode(key_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("MESSENGER_TOKEN_DEK is not valid base64") from exc
        return cls(key)

    @staticmethod
    def generate_key_b64() -> str:
        """Mint a fresh key for ``MESSENGER_TOKEN_DEK`` (operator convenience)."""
        return base64.b64encode(nacl_random(SecretBox.KEY_SIZE)).decode("ascii")

    def encrypt(self, plaintext: bytes) -> bytes:
        # ``SecretBox.encrypt`` returns an ``EncryptedMessage`` whose bytes are
        # ``nonce (24) || ciphertext`` — the full self-contained wire form.
        return bytes(self._box.encrypt(plaintext))

    def decrypt(self, ciphertext: bytes) -> bytes:
        if len(ciphertext) < _NONCE_LEN + SecretBox.MACBYTES:
            raise EnvelopeDecryptError("ciphertext too short")
        try:
            return self._box.decrypt(ciphertext)
        except (CryptoError, ValueError) as exc:
            raise EnvelopeDecryptError("ciphertext failed to open") from exc

    def encrypt_b64(self, plaintext: bytes) -> str:
        return base64.b64encode(self.encrypt(plaintext)).decode("ascii")

    def decrypt_b64(self, ciphertext_b64: str) -> bytes:
        try:
            raw = base64.b64decode(ciphertext_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EnvelopeDecryptError("ciphertext is not valid base64") from exc
        return self.decrypt(raw)


def make_envelope(settings: Settings) -> EnvelopeCipher | None:
    """Build the messenger envelope from settings.

    Returns ``None`` when the messenger feature is disabled
    (``messenger_long_poll_enabled`` off) so no crypto material is held.

    Missing key policy (deliberately NOT a boot crash): the messenger
    integration is optional, so a missing ``MESSENGER_TOKEN_DEK`` must never
    take down the whole API. Self-hosted gets an ephemeral key + a loud warning
    (bots need re-binding after a restart). Hosted mode — where an ephemeral
    key would silently brick every connected bot on every deploy — disables
    the feature instead (``None``), logging the fix. The endpoints then 503
    until the operator sets the DEK. A lost DEK on an already-connected bot is
    *already* handled gracefully: the poller's ``_plaintext_token`` raises
    ``EnvelopeDecryptError`` → the row is marked ``error`` → the user re-binds.
    No silent data loss, so a hard boot failure is unnecessary.
    """
    if not settings.messenger_long_poll_enabled:
        return None
    key_b64 = settings.messenger_token_dek.strip()
    if key_b64:
        return EnvelopeCipher.from_base64(key_b64)
    if settings.deployment_mode == "hosted":
        logger.error(
            "MESSENGER_TOKEN_DEK not set in hosted mode — messenger integration "
            "is DISABLED (endpoints will 503). Generate a key with "
            "EnvelopeCipher.generate_key_b64() and set MESSENGER_TOKEN_DEK in the "
            "environment to enable Telegram bots."
        )
        return None
    ephemeral = EnvelopeCipher.from_base64(EnvelopeCipher.generate_key_b64())
    logger.warning(
        "MESSENGER_TOKEN_DEK not set — using an ephemeral envelope key. "
        "All connected messenger bots will need re-binding after a restart."
    )
    return ephemeral


__all__ = ["EnvelopeCipher", "EnvelopeDecryptError", "make_envelope"]
