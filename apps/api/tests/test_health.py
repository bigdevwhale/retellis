"""``/v1/health`` contract: status ok, langfuse field present, ECDH pub is base64."""

from __future__ import annotations

import base64


async def test_health_ok(client):
    r = await client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["langfuse"] in {"ok", "disabled"}
    pub = body["ecdh_pub"]
    assert isinstance(pub, str) and len(pub) > 20
    # The published value is a base64-encoded X25519 public key (32 bytes).
    raw = base64.b64decode(pub)
    assert len(raw) == 32
