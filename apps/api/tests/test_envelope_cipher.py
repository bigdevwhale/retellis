"""Envelope cipher: round-trip, tamper detection, key handling, make_envelope policy."""

from __future__ import annotations

import base64

import pytest

from ai_companion_api.config import Settings
from ai_companion_api.crypto.envelope import (
    EnvelopeCipher,
    EnvelopeDecryptError,
    make_envelope,
)


def _fresh_cipher() -> EnvelopeCipher:
    return EnvelopeCipher.from_base64(EnvelopeCipher.generate_key_b64())


def test_roundtrip_bytes() -> None:
    c = _fresh_cipher()
    secret = b"123456789:ABC-DEF1234ghijkl-XYZ"
    ct = c.encrypt(secret)
    assert ct != secret
    assert c.decrypt(ct) == secret


def test_roundtrip_b64() -> None:
    c = _fresh_cipher()
    secret = b"hello-telegram-bot-token"
    blob = c.encrypt_b64(secret)
    # base64 text only — safe for a Text column.
    assert all(ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/-=" for ch in blob)
    assert c.decrypt_b64(blob) == secret


def test_ciphertext_is_unique_per_encrypt() -> None:
    """XSalsa20 uses a fresh nonce per encrypt — same plaintext → different ct."""
    c = _fresh_cipher()
    secret = b"same-token"
    a = c.encrypt(secret)
    b = c.encrypt(secret)
    assert a != b
    assert c.decrypt(a) == secret
    assert c.decrypt(b) == secret


def test_tamper_detected() -> None:
    c = _fresh_cipher()
    ct = bytearray(c.encrypt(b"sensitive"))
    ct[-1] ^= 0xFF  # flip a MAC byte
    with pytest.raises(EnvelopeDecryptError):
        c.decrypt(bytes(ct))


def test_wrong_key_cannot_decrypt() -> None:
    c1 = _fresh_cipher()
    c2 = _fresh_cipher()
    ct = c1.encrypt(b"token")
    with pytest.raises(EnvelopeDecryptError):
        c2.decrypt(ct)


def test_truncated_ciphertext_rejected() -> None:
    c = _fresh_cipher()
    with pytest.raises(EnvelopeDecryptError):
        c.decrypt(b"too-short")
    with pytest.raises(EnvelopeDecryptError):
        c.decrypt_b64("bm90YmFzZTY0")  # valid b64 but too short


def test_invalid_base64_key_rejected() -> None:
    with pytest.raises(ValueError, match="not valid base64"):
        EnvelopeCipher.from_base64("!!!not-base64!!!")


def test_wrong_key_length_rejected() -> None:
    with pytest.raises(ValueError):
        EnvelopeCipher(b"short")


def test_generate_key_b64_round_trips() -> None:
    b64 = EnvelopeCipher.generate_key_b64()
    raw = base64.b64decode(b64)
    assert len(raw) == 32  # SecretBox.KEY_SIZE


def _settings(**overrides) -> Settings:
    base = Settings()
    return base.model_copy(update=overrides)


def test_make_envelope_with_key() -> None:
    key_b64 = EnvelopeCipher.generate_key_b64()
    env = make_envelope(_settings(messenger_token_dek=key_b64))
    assert env is not None
    assert env.decrypt(env.encrypt(b"x")) == b"x"


def test_make_envelope_disabled_returns_none() -> None:
    env = make_envelope(_settings(messenger_long_poll_enabled=False, messenger_token_dek=""))
    assert env is None


def test_make_envelope_hosted_without_key_disabled() -> None:
    # Hosted mode without a DEK disables the feature (None) instead of crashing
    # the boot — the messenger integration is optional and must not take down
    # the whole API. Endpoints 503 until the operator sets MESSENGER_TOKEN_DEK.
    env = make_envelope(_settings(deployment_mode="hosted", messenger_token_dek=""))
    assert env is None


def test_make_envelope_self_hosted_without_key_is_ephemeral() -> None:
    env = make_envelope(_settings(deployment_mode="self_hosted", messenger_token_dek=""))
    assert env is not None
    # Ephemeral key still works for the process lifetime.
    assert env.decrypt(env.encrypt(b"x")) == b"x"