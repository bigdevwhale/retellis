"""``PUT /v1/family/providers/{pid}/enc_blob`` — the vault-rotation re-seal
endpoint.

Owner-only, family-scoped. Replaces ONLY the ``enc_blob`` column on a family
provider row. Used by the client-side vault rotation: the client decrypts the
provider key under the OLD family master key, re-seals it under the NEW family
master key (new passphrase + new salt), and PUTs the fresh opaque base64
ciphertext here so the server-side backup tracks the rotation. ``key_handle``
is unchanged so the row stays valid.

Security invariants pinned here (CLAUDE.md):
  - The body is opaque base64 ciphertext only — a plaintext-looking
    ``sk-...`` value is rejected at the door so the column NEVER stores a
    raw key.
  - Cross-family / non-owner access is 404 (not 403), mirroring
    ``delete_family_provider``.
  - ``key_handle`` is untouched by this endpoint (rotation keeps the row
    valid; only the ciphertext is re-sealed).
"""

from __future__ import annotations

from contextlib import asynccontextmanager


def _new_client(make_app, app_client):
    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with app_client(app) as c:
            yield c

    return _ctx()


async def _signup(ac, email: str) -> str:
    r = await ac.post("/v1/auth/signup", json={"email": email, "password": "pwaaaaaaaaaa"})
    assert r.status_code in (200, 201), r.text
    me = await ac.get("/v1/auth/me")
    return me.json()["user_id"]


async def _new_family(ac, name: str = "Cohort") -> dict:
    r = await ac.post("/v1/family", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


async def _make_provider(ac, enc_blob: str = "ZmFrZS1jaXBoZXJ0ZXh0") -> dict:
    r = await ac.post(
        "/v1/family/providers",
        json={
            "kind": "openai",
            "label": "Family key",
            "key_handle": "fam-kh-1",
            "model": "gpt-4o-mini",
            "enc_blob": enc_blob,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# A real sealed blob is base64 of salt||nonce||ct — it never starts with the
# plaintext key prefix. Use a value that is clearly NOT a raw key.
NEW_BLOB = "bmV3LXJvdGF0ZWQtY2lwaGVydGV4dA=="  # base64 of "new-rotated-ciphertext"


async def test_owner_can_replace_enc_blob(make_app, app_client) -> None:
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        provider = await _make_provider(ac)
        r = await ac.put(
            f"/v1/family/providers/{provider['id']}/enc_blob",
            json={"enc_blob": NEW_BLOB},
        )
        assert r.status_code == 200, r.text
        updated = r.json()
        assert updated["enc_blob"] == NEW_BLOB
        # key_handle is unchanged — rotation keeps the server row valid.
        assert updated["key_handle"] == provider["key_handle"]
        assert updated["id"] == provider["id"]
        assert updated["kind"] == provider["kind"]
        # And the new blob is persisted (a fresh GET reflects it).
        r2 = await ac.get("/v1/family/providers")
        listed = r2.json()
        assert any(p["enc_blob"] == NEW_BLOB for p in listed)


async def test_enc_blob_rejects_plaintext_key(make_app, app_client) -> None:
    """The column must NEVER store a raw ``sk-...`` key. A plaintext-looking
    body is rejected with 400 and the column is unchanged."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        provider = await _make_provider(ac, enc_blob="ZmFrZS1vbGQtYmxvYg==")
        r = await ac.put(
            f"/v1/family/providers/{provider['id']}/enc_blob",
            json={"enc_blob": "sk-proj-1234567890abcdefXYZ"},
        )
        assert r.status_code == 400, r.text
        # The rejection message is generic — it must not echo the key.
        assert "sk-proj" not in r.text
        # The column retains the old blob (the PUT was rejected).
        r2 = await ac.get("/v1/family/providers")
        listed = r2.json()
        match = [p for p in listed if p["id"] == provider["id"]][0]
        assert match["enc_blob"] == "ZmFrZS1vbGQtYmxvYg=="


async def test_enc_blob_rejects_empty(make_app, app_client) -> None:
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        provider = await _make_provider(ac)
        r = await ac.put(
            f"/v1/family/providers/{provider['id']}/enc_blob",
            json={"enc_blob": ""},
        )
        assert r.status_code == 400, r.text


async def test_enc_blob_non_owner_404(make_app, app_client) -> None:
    """A stranger (no family) gets 404, not 403 — mirroring
    ``delete_family_provider`` (cross-family access is indistinguishable
    from "doesn't exist")."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        provider = await _make_provider(ac)
    async with _new_client(make_app, app_client) as ac_stranger:
        await _signup(ac_stranger, "stranger@x.com")
        r = await ac_stranger.put(
            f"/v1/family/providers/{provider['id']}/enc_blob",
            json={"enc_blob": NEW_BLOB},
        )
        assert r.status_code == 404, r.text


async def test_enc_blob_unknown_provider_404(make_app, app_client) -> None:
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.put(
            "/v1/family/providers/does-not-exist/enc_blob",
            json={"enc_blob": NEW_BLOB},
        )
        assert r.status_code == 404, r.text
