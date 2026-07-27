"""Vault: ECDH sealed-box round-trip + in-memory zeroization."""

from __future__ import annotations

import base64
import json

import pytest
from nacl.public import SealedBox

from ai_companion_api.vault.decrypt import DecryptError, decrypt_key_blob, decrypted_key
from ai_companion_api.vault.session_ecdh import generate_session_keypair
from ai_companion_api.vault.zeroize import zeroized


def _seal_with_pub(payload: dict, pub_bytes: bytes) -> str:
    from nacl.public import PublicKey

    box = SealedBox(PublicKey(pub_bytes))
    return base64.b64encode(box.encrypt(json.dumps(payload).encode("utf-8"))).decode("ascii")


def test_ecdh_roundtrip() -> None:
    kp = generate_session_keypair()
    pub_bytes = base64.b64decode(kp.pub_b64)
    blob = _seal_with_pub(
        {"provider_kind": "openai", "api_key": "sk-test-1234567890abcdef", "base_url": None},
        pub_bytes,
    )
    dk = decrypt_key_blob(blob, kp.private_key)
    assert dk.provider_kind == "openai"
    assert dk.api_key_str() == "sk-test-1234567890abcdef"
    assert dk.base_url is None


def test_zeroize_after_context() -> None:
    kp = generate_session_keypair()
    pub_bytes = base64.b64decode(kp.pub_b64)
    blob = _seal_with_pub(
        {"provider_kind": "openai", "api_key": "sk-secret-AAAAAAAAAAAA"},
        pub_bytes,
    )
    key_len = len(b"sk-secret-AAAAAAAAAAAA")
    with decrypted_key(blob, kp.private_key) as dk:
        assert dk.api_key_str() == "sk-secret-AAAAAAAAAAAA"
        assert bytes(dk.api_key) == b"sk-secret-AAAAAAAAAAAA"
    # After the context exits the source bytearray must be wiped.
    assert bytes(dk.api_key) == b"\x00" * key_len


def test_zeroized_helper_wipes_bytearray() -> None:
    buf = bytearray(b"sk-sensitive-key-material")
    with zeroized(buf):
        assert bytes(buf) == b"sk-sensitive-key-material"
    assert bytes(buf) == b"\x00" * len(b"sk-sensitive-key-material")
    # On exception too.
    buf2 = bytearray(b"sk-another-key")
    with pytest.raises(RuntimeError):
        with zeroized(buf2):
            raise RuntimeError("boom")
    assert bytes(buf2) == b"\x00" * len(b"sk-another-key")


def test_bad_blob_raises_redacted_error() -> None:
    kp = generate_session_keypair()
    # Not a valid sealed box for this key.
    bad = base64.b64encode(b"definitely not a sealed box payload").decode("ascii")
    with pytest.raises(DecryptError) as exc:
        decrypt_key_blob(bad, kp.private_key)
    assert "sk-" not in str(exc.value)


def test_adapter_holds_bytearray_and_zeroizes_with_caller() -> None:
    """K1: the LiteLLMAdapter must hold the BYOK bytearray (the same buffer the
    caller zeroizes), NOT a decoded str. A decoded str on ``self`` would survive
    the caller's ``zeroized()`` window for the whole request. The adapter must
    decode per-call into a short-lived local. Zeroizing the caller's bytearray
    must wipe the adapter's reference too (shared buffer)."""
    from ai_companion_api.llm.litellm_adapter import LiteLLMAdapter

    buf = bytearray(b"sk-byok-secret-key-material")
    adapter = LiteLLMAdapter("openai", buf, base_url=None)
    # The adapter holds the bytearray itself, not a decoded str.
    assert isinstance(adapter._api_key, bytearray)
    assert adapter._api_key is buf  # same buffer the caller will zeroize
    # Per-call decode returns the plaintext without caching it on self.
    assert adapter._api_key_str() == "sk-byok-secret-key-material"
    # The caller zeroizes its bytearray; the adapter's reference is the same
    # buffer, so it is wiped too — no surviving str copy on the adapter.
    with zeroized(buf):
        assert adapter._api_key_str() == "sk-byok-secret-key-material"
    assert bytes(adapter._api_key) == b"\x00" * len(b"sk-byok-secret-key-material")
    # After zeroize, the plaintext is no longer reachable through the adapter:
    # decoding the wiped buffer yields null bytes, never the secret.
    assert adapter._api_key_str() == "\x00" * len(b"sk-byok-secret-key-material")
    assert "sk-byok" not in adapter._api_key_str()


def test_adapter_accepts_env_str() -> None:
    """Env-fallback keys are process-lifetime str (not zeroized per the security
    model). The adapter must accept str too and return it as-is per call."""
    from ai_companion_api.llm.litellm_adapter import LiteLLMAdapter

    adapter = LiteLLMAdapter("anthropic", "sk-ant-envkey", base_url=None)
    assert isinstance(adapter._api_key, str)
    assert adapter._api_key_str() == "sk-ant-envkey"
