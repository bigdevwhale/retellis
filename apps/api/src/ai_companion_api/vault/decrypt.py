"""ECDH decryption of the per-request BYOK key blob.

The client seals a JSON payload ``{"provider_kind","api_key","base_url"?}`` with
the server's X25519 public key (published at ``GET /v1/health``) using libsodium
``crypto_box_seal`` (X25519 + XSalsa20-Poly1305). We open it with the server
session private key, parse the JSON, and return the key as a ``bytearray`` so
the caller can zeroize it. The plaintext is never copied into an immutable
``str`` that could be retained by the GC.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from nacl.public import PrivateKey, SealedBox

from .zeroize import zeroized


@dataclass
class DecryptedKey:
    provider_kind: str
    api_key: bytearray
    base_url: str | None
    # Optional provider-specific extras (e.g. AWS Bedrock's secret access key +
    # region; Azure's api_version). Populated from ``enc_key_blob`` JSON; missing
    # on every kind that doesn't need it. Treated as opaque to the chain — the
    # adapter picks what it needs. The router zeroizes the bytearray on exit;
    # the strings here are short-lived and the same honest-limit caveat as
    # ``api_key`` applies.
    extra: dict[str, str] | None = None

    def api_key_str(self) -> str:
        """Decode for the one LiteLLM call; caller must zeroize afterwards."""
        return self.api_key.decode("utf-8")


class DecryptError(Exception):
    """Raised when the blob cannot be opened or parsed. Never carries key material."""


def _wipe(buf: bytes | bytearray | None) -> None:
    if buf is None:
        return
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0


def decrypt_key_blob(blob_b64: str, server_priv: PrivateKey) -> DecryptedKey:
    """Open a sealed ``enc_key_blob`` and return its fields.

    Raises ``DecryptError`` on any failure (bad base64, bad seal, bad JSON,
    missing fields). The error message is redacted and contains no key material.
    """
    try:
        blob = base64.b64decode(blob_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise DecryptError("malformed key blob (base64)") from exc

    try:
        plaintext = SealedBox(server_priv).decrypt(blob)
    except Exception as exc:  # nacl raises CryptoError; treat all as bad blob
        raise DecryptError("could not open key blob (seal/recipient mismatch)") from exc

    try:
        payload = json.loads(plaintext)
        kind = payload["provider_kind"]
        api_key = payload["api_key"]
        base_url = payload.get("base_url")
        # Optional provider-specific metadata. Whitelist-only is the safe
        # default, but for now we forward an arbitrary ``extra`` map because
        # the BYOK picker is the only writer and only adds known keys
        # (``aws_secret_access_key``, ``aws_region_name``, ``api_version``).
        # Future per-kind schemas can tighten this without changing the wire
        # format.
        extra_raw = payload.get("extra")
        extra: dict[str, str] | None
        if isinstance(extra_raw, dict):
            extra = {str(k): str(v) for k, v in extra_raw.items() if v is not None}
        else:
            extra = None
    except (ValueError, KeyError, TypeError) as exc:
        raise DecryptError("malformed key blob (payload shape)") from exc

    if not isinstance(kind, str) or not isinstance(api_key, str) or api_key == "":
        raise DecryptError("malformed key blob (payload values)")

    key_buf = bytearray(api_key.encode("utf-8"))
    return DecryptedKey(provider_kind=kind, api_key=key_buf, base_url=base_url, extra=extra)


def parse_decrypted_key(plaintext: bytes) -> DecryptedKey:
    """Parse an already-decrypted key JSON payload into a ``DecryptedKey``.

    This is the second half of ``decrypt_key_blob`` factored out so the
    server-side envelope path (``providers.api_key_ciphertext``) can reuse it:
    the envelope stores the same JSON payload the ECDH-sealed blob carried, so
    after ``envelope.decrypt_b64`` we parse it here without re-running the
    SealedBox open. Raises ``DecryptError`` on any shape problem (the message
    is redacted and carries no key material).
    """
    try:
        payload = json.loads(plaintext)
        kind = payload["provider_kind"]
        api_key = payload["api_key"]
        base_url = payload.get("base_url")
        extra_raw = payload.get("extra")
        extra: dict[str, str] | None
        if isinstance(extra_raw, dict):
            extra = {str(k): str(v) for k, v in extra_raw.items() if v is not None}
        else:
            extra = None
    except (ValueError, KeyError, TypeError) as exc:
        raise DecryptError("malformed key blob (payload shape)") from exc
    if not isinstance(kind, str) or not isinstance(api_key, str) or api_key == "":
        raise DecryptError("malformed key blob (payload values)")
    key_buf = bytearray(api_key.encode("utf-8"))
    return DecryptedKey(provider_kind=kind, api_key=key_buf, base_url=base_url, extra=extra)


@contextmanager
def decrypted_key(blob_b64: str, server_priv: PrivateKey) -> Iterator[DecryptedKey]:
    """Decrypt a blob, yield the ``DecryptedKey``, and zeroize it on exit.

    Use this around the single LiteLLM call so the key bytes are wiped even if
    the call raises. Never hold the ``DecryptedKey`` outside this block.
    """
    dk = decrypt_key_blob(blob_b64, server_priv)
    with zeroized(dk.api_key):
        yield dk
