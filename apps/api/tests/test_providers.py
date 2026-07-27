"""``/v1/providers`` — metadata CRUD stores key_handle only, never the key."""

from __future__ import annotations

import pytest


async def test_create_list_delete_provider(client) -> None:
    # Create a provider with only a key_handle (the key itself lives client-side).
    r = await client.post(
        "/v1/providers",
        json={"kind": "openai", "label": "Work OpenAI", "key_handle": "kh-abc123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "openai"
    assert body["label"] == "Work OpenAI"
    assert body["key_handle"] == "kh-abc123"
    pid = body["id"]
    # The response must never carry an api_key field.
    assert "api_key" not in body

    listing = await client.get("/v1/providers")
    assert listing.status_code == 200
    assert any(p["id"] == pid for p in listing.json())

    dele = await client.delete(f"/v1/providers/{pid}")
    assert dele.status_code == 204

    listing2 = await client.get("/v1/providers")
    assert all(p["id"] != pid for p in listing2.json())


async def test_delete_unknown_returns_404(client) -> None:
    r = await client.delete("/v1/providers/does-not-exist")
    assert r.status_code == 404


# --- enc_blob: zero-knowledge at-rest key backup (survives a cache wipe) -----
#
# The server stores an encrypted key blob it cannot decrypt — XChaCha20-Poly1305
# ciphertext keyed by the user's passphrase (never sent). These tests assert the
# blob is treated as opaque (stored + returned verbatim, never decoded) and that
# legacy rows without it still work. Runs against the in-memory store default.

# A pretend ciphertext blob — the server must store and return it verbatim
# without attempting to decode or interpret it. (Real blobs are base64 of
# salt||nonce||ct produced client-side by lib/vault.ts.)
_OPAQUE_BLOB = "cj1wbGVhc2Utbm90LWEtcGxhaW50ZXh0LWtleQ=="


async def test_create_with_enc_blob_round_trips_opaque(client) -> None:
    r = await client.post(
        "/v1/providers",
        json={
            "kind": "openai",
            "label": "Work OpenAI",
            "key_handle": "kh-1",
            "model": "gpt-4o-mini",
            "enc_blob": _OPAQUE_BLOB,
        },
    )
    assert r.status_code == 200
    p = r.json()
    assert p["enc_blob"] == _OPAQUE_BLOB  # verbatim, untouched
    assert p["key_handle"] == "kh-1"

    # GET returns the blob so a cache-wiped client can restore from it.
    rows = (await client.get("/v1/providers")).json()
    assert len(rows) == 1
    assert rows[0]["enc_blob"] == _OPAQUE_BLOB


async def test_create_without_enc_blob_still_works(client) -> None:
    # Legacy / mock-provider rows don't send enc_blob — must remain valid.
    r = await client.post(
        "/v1/providers",
        json={"kind": "ollama", "label": "Local", "key_handle": "kh-2", "model": None},
    )
    assert r.status_code == 200
    assert r.json()["enc_blob"] is None


async def test_delete_provider_drops_enc_blob(client) -> None:
    created = (
        await client.post(
            "/v1/providers",
            json={
                "kind": "openai",
                "label": "Gone",
                "key_handle": "kh-3",
                "enc_blob": _OPAQUE_BLOB,
            },
        )
    ).json()
    r = await client.delete(f"/v1/providers/{created['id']}")
    assert r.status_code == 204
    # Removing the provider also drops its enc_blob — no at-rest backup lingers.
    assert (await client.get("/v1/providers")).json() == []


@pytest.mark.asyncio
async def test_inmemory_store_round_trips_enc_blob() -> None:
    from ai_companion_contracts import Provider, ProviderKind

    from ai_companion_api.memory.store import InMemoryStore

    store = InMemoryStore()
    p = Provider(
        id="p1",
        user_id="u",
        kind=ProviderKind.openai,
        label="L",
        base_url=None,
        key_handle="kh-x",
        model=None,
        enc_blob=_OPAQUE_BLOB,
    )
    out = await store.add_provider(p)
    assert out.enc_blob == _OPAQUE_BLOB

    listed = await store.list_providers(user_id="u")
    assert listed[0].enc_blob == _OPAQUE_BLOB

    got = await store.get_provider(user_id="u", provider_id="p1")
    assert got is not None and got.enc_blob == _OPAQUE_BLOB

    assert await store.delete_provider(user_id="u", provider_id="p1") is True
    assert await store.get_provider(user_id="u", provider_id="p1") is None
    # Second delete is a no-op (already gone) → False, not an error.
    assert await store.delete_provider(user_id="u", provider_id="p1") is False


# --- PATCH: partial update of label / model / base_url ------------------------
#
# Inline edit on the Keys & setup summary card. Only metadata mutates —
# key_handle and the zero-knowledge enc_blob are immutable here (the API key
# itself never travels through this path; it stays in the client vault).


async def test_patch_provider_updates_label_model_baseurl(client) -> None:
    created = (
        await client.post(
            "/v1/providers",
            json={"kind": "openai", "label": "Old", "key_handle": "kh-patch-1"},
        )
    ).json()
    res = await client.patch(
        f"/v1/providers/{created['id']}",
        json={"label": "New", "model": "gpt-4.1", "base_url": "https://x"},
    )
    assert res.status_code == 200, await res.text()
    body = res.json()
    assert body["label"] == "New"
    assert body["model"] == "gpt-4.1"
    assert body["base_url"] == "https://x"
    # key_handle is untouched (the key itself is still in the client vault).
    assert body["key_handle"] == "kh-patch-1"
    # Round-trip via list to confirm server-side persistence, not just a
    # response-only mutation.
    listed = (await client.get("/v1/providers")).json()
    assert listed[0]["label"] == "New"
    assert listed[0]["model"] == "gpt-4.1"
    assert listed[0]["base_url"] == "https://x"
    assert listed[0]["key_handle"] == "kh-patch-1"


async def test_patch_provider_partial_only_supplied_fields_change(client) -> None:
    created = (
        await client.post(
            "/v1/providers",
            json={"kind": "openai", "label": "L", "key_handle": "kh-patch-2", "model": "gpt-4o"},
        )
    ).json()
    # Send only ``label`` — model + base_url must keep their existing values.
    res = await client.patch(f"/v1/providers/{created['id']}", json={"label": "L2"})
    assert res.status_code == 200
    body = res.json()
    assert body["label"] == "L2"
    assert body["model"] == "gpt-4o"  # unchanged
    assert body["base_url"] is None  # unchanged


async def test_patch_provider_missing_returns_404(client) -> None:
    res = await client.patch(
        "/v1/providers/does-not-exist",
        json={"label": "X"},
    )
    assert res.status_code == 404


async def test_provider_embeddings_model_round_trip(client) -> None:
    # BYOK semantic memory: embeddings_model is plain metadata (a model id,
    # never a key). Set on create, changed via PATCH, cleared with explicit null.
    created = (
        await client.post(
            "/v1/providers",
            json={
                "kind": "openai",
                "label": "Emb",
                "key_handle": "kh-emb-1",
                "embeddings_model": "text-embedding-3-small",
            },
        )
    ).json()
    assert created["embeddings_model"] == "text-embedding-3-small"

    res = await client.patch(
        f"/v1/providers/{created['id']}",
        json={"embeddings_model": "text-embedding-3-large"},
    )
    assert res.status_code == 200
    assert res.json()["embeddings_model"] == "text-embedding-3-large"
    # Other fields untouched by the embeddings-only PATCH.
    assert res.json()["label"] == "Emb"

    # Explicit null turns semantic memory off for this provider.
    res = await client.patch(f"/v1/providers/{created['id']}", json={"embeddings_model": None})
    assert res.status_code == 200
    assert res.json()["embeddings_model"] is None

    # Absent key leaves the value alone.
    await client.patch(
        f"/v1/providers/{created['id']}", json={"embeddings_model": "text-embedding-3-small"}
    )
    res = await client.patch(f"/v1/providers/{created['id']}", json={"label": "Emb2"})
    assert res.json()["embeddings_model"] == "text-embedding-3-small"


async def test_patch_provider_explicit_null_clears_baseurl(client) -> None:
    created = (
        await client.post(
            "/v1/providers",
            json={
                "kind": "openai",
                "label": "L",
                "key_handle": "kh-patch-3",
                "base_url": "https://x",
            },
        )
    ).json()
    # Sending ``"base_url": null`` explicitly must clear the column, distinct
    # from "field absent" (which would leave the existing value alone).
    res = await client.patch(
        f"/v1/providers/{created['id']}",
        json={"base_url": None},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["base_url"] is None
    assert body["label"] == "L"  # unchanged
    assert body["key_handle"] == "kh-patch-3"  # unchanged
