"""Server-side X25519 session keypair.

Generated once at startup (lifespan) and held in ``app.state``. The public key
is published at ``GET /v1/health`` so clients can ECDH-encrypt their BYOK key
blob. The private key never leaves the process and is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass

from nacl.public import PrivateKey


@dataclass(frozen=True)
class SessionECDH:
    priv_b64: str
    pub_b64: str
    _priv: PrivateKey  # noqa: PYI044 — held internally, never serialized

    @property
    def private_key(self) -> PrivateKey:
        return self._priv


def generate_session_keypair() -> SessionECDH:
    import base64

    priv = PrivateKey.generate()
    pub = priv.public_key
    return SessionECDH(
        priv_b64=base64.b64encode(bytes(priv)).decode(),
        pub_b64=base64.b64encode(bytes(pub)).decode(),
        _priv=priv,
    )
