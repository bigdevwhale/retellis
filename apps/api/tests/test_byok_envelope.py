"""Server-side BYOK envelope storage (migration 0023).

Covers the new flow: the client ECDH-seals the API key ONCE at onboarding
(``enc_key_blob`` on ``POST /v1/providers``), the server decrypts it with the
session private key, re-wraps it under ``MESSENGER_TOKEN_DEK``
(``crypto/envelope.py::EnvelopeCipher``), and stores
``providers.api_key_ciphertext``. Per turn the server envelope-decrypts the
ciphertext (no per-turn client blob needed), builds a ``DecryptedKey``, and
zeroizes after the chain runs.

Security invariants pinned here (CLAUDE.md):
  - ``api_key_ciphertext`` is NEVER returned by ``GET /v1/providers`` — only
    ``key_handle`` (the contract ``Provider`` model has no ciphertext field).
  - The plaintext key is never logged; ``grep -r 'sk-' deploy/`` stays empty.
  - A tampered/corrupted ciphertext raises ``EnvelopeDecryptError`` which the
    per-turn path catches → fallback/mock (never 500).
  - Cross-user scoping: a stranger's ``api_key_ciphertext`` is invisible (the
    store lookup filters on ``user_id``; a miss → ``None`` → env-fallback chain).
  - Hosted mode without ``MESSENGER_TOKEN_DEK`` 503s on the create endpoint
    (``make_envelope`` returns None → refuse rather than store an unprotected key).
"""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager

import pytest
from nacl.public import PublicKey, SealedBox


def _seal_key(payload: dict, pub_b64: str) -> str:
    """ECDH-seal a key JSON payload to the server session pubkey (libsodium
    ``crypto_box_seal`` — the same primitive the client uses at onboarding)."""
    pub = PublicKey(base64.b64decode(pub_b64))
    return base64.b64encode(SealedBox(pub).encrypt(json.dumps(payload).encode("utf-8"))).decode(
        "ascii"
    )


@asynccontextmanager
async def _client_ctx(make_app, app_client):
    """Build an app with MESSENGER_LONG_POLL_ENABLED=1 + a real DEK so the
    envelope is configured (hosted disabled path is covered separately)."""
    import os

    dek = os.environ.get("MESSENGER_TOKEN_DEK")
    os.environ["MESSENGER_TOKEN_DEK"] = base64.b64encode(b"k" * 32).decode()
    os.environ["MESSENGER_LONG_POLL_ENABLED"] = "1"
    try:
        app = make_app(MESSENGER_TOKEN_DEK=base64.b64encode(b"k" * 32).decode(), MESSENGER_LONG_POLL_ENABLED="1")
        async with app_client(app) as c:
            yield c
    finally:
        if dek is None:
            os.environ.pop("MESSENGER_TOKEN_DEK", None)
        else:
            os.environ["MESSENGER_TOKEN_DEK"] = dek
        os.environ["MESSENGER_LONG_POLL_ENABLED"] = "0"


async def _signup(ac, email: str) -> str:
    r = await ac.post("/v1/auth/signup", json={"email": email, "password": "pwaaaaaaaaaa"})
    assert r.status_code in (200, 201), r.text
    me = await ac.get("/v1/auth/me")
    return me.json()["user_id"]


# --- create: enc_key_blob → api_key_ciphertext populated ----------------------


async def test_create_provider_with_enc_key_blob_stores_ciphertext(make_app, app_client) -> None:
    async with _client_ctx(make_app, app_client) as ac:
        uid = await _signup(ac, "owner@x.com")
        # Fetch the server session pubkey.
        health = await ac.get("/v1/health")
        pub_b64 = health.json()["ecdh_pub"]
        blob = _seal_key(
            {"provider_kind": "openai", "api_key": "sk-test-1234567890abcdef", "base_url": None},
            pub_b64,
        )
        r = await ac.post(
            "/v1/providers",
            json={
                "kind": "openai",
                "label": "Work",
                "key_handle": "kh-1",
                "enc_key_blob": blob,
            },
        )
        assert r.status_code == 200, r.text
        p = r.json()
        # The response must never carry api_key_ciphertext or the plaintext key.
        assert "api_key_ciphertext" not in p
        assert "api_key" not in p
        assert p["key_handle"] == "kh-1"
        pid = p["id"]

        # GET /providers also never leaks the ciphertext.
        rows = (await ac.get("/v1/providers")).json()
        assert len(rows) == 1
        assert "api_key_ciphertext" not in rows[0]
        assert "api_key" not in rows[0]

        # The in-memory store DID persist the ciphertext (inspect app.state).
        store = ac._transport.app.state.store  # type: ignore[attr-defined]
        ct = await store.get_provider_api_key_ciphertext(user_id=uid, key_handle="kh-1")
        assert ct is not None and ct != ""
        # The ciphertext is base64 (no plaintext key prefix).
        assert "sk-" not in ct


async def test_create_provider_without_enc_key_blob_leaves_ciphertext_null(
    make_app, app_client,
) -> None:
    async with _client_ctx(make_app, app_client) as ac:
        uid = await _signup(ac, "legacy@x.com")
        r = await ac.post(
            "/v1/providers",
            json={"kind": "openai", "label": "Legacy", "key_handle": "kh-legacy"},
        )
        assert r.status_code == 200, r.text
        store = ac._transport.app.state.store  # type: ignore[attr-defined]
        assert await store.get_provider_api_key_ciphertext(user_id=uid, key_handle="kh-legacy") is None


# --- hosted mode without DEK → 503 -------------------------------------------


async def test_create_provider_503_when_envelope_not_configured(make_app, app_client) -> None:
    """When the envelope DEK is not configured (``app.state.envelope is None``),
    the create endpoint 503s rather than storing a key it can't protect.

    Simulates the hosted-mode + missing ``MESSENGER_TOKEN_DEK`` case by nulling
    ``app.state.envelope`` after boot (the same state ``make_envelope`` produces
    in hosted mode without a DEK). Uses the self-hosted local backend so signup
    works without an email transport."""
    async with _client_ctx(make_app, app_client) as ac:
        await _signup(ac, "hosted@x.com")
        # Simulate the missing-DEK hosted case: the envelope is None.
        ac._transport.app.state.envelope = None  # type: ignore[attr-defined]
        health = await ac.get("/v1/health")
        pub_b64 = health.json()["ecdh_pub"]
        blob = _seal_key(
            {"provider_kind": "openai", "api_key": "sk-test-1234567890abcdef", "base_url": None},
            pub_b64,
        )
        r = await ac.post(
            "/v1/providers",
            json={
                "kind": "openai",
                "label": "Hosted",
                "key_handle": "kh-h",
                "enc_key_blob": blob,
            },
        )
        assert r.status_code == 503, r.text
        assert "envelope" in r.json()["detail"].lower()


# --- per-turn resolution from envelope (byok_enc_key_blob=None) --------------


async def test_per_turn_resolves_from_envelope_when_no_blob(make_app, app_client) -> None:
    """The new client sends ``enc_key_blob=None`` + ``key_handle``; the server
    resolves the BYOK key from ``api_key_ciphertext`` and the chain uses it.

    Verified at the resolution + ``build_chain`` level (not a full stream turn,
    which would need litellm installed): the envelope-decrypted key lands as the
    first (BYOK) candidate with the right kind + key, and the ``decrypted``
    field carries a ``DecryptedKey`` whose ``api_key`` bytearray is the plaintext."""
    from ai_companion_api.llm.provider import build_chain
    from ai_companion_api.routers.llm import _resolve_byok_from_envelope
    from ai_companion_api.vault.decrypt import DecryptedKey

    async with _client_ctx(make_app, app_client) as ac:
        uid = await _signup(ac, "turn@x.com")
        health = await ac.get("/v1/health")
        pub_b64 = health.json()["ecdh_pub"]
        blob = _seal_key(
            {"provider_kind": "openai", "api_key": "sk-byok-turn-1234567890", "base_url": None},
            pub_b64,
        )
        # Store the key server-side via the create endpoint.
        r = await ac.post(
            "/v1/providers",
            json={"kind": "openai", "label": "Turn", "key_handle": "kh-turn", "enc_key_blob": blob},
        )
        assert r.status_code == 200, r.text

        app = ac._transport.app  # type: ignore[attr-defined]
        store = app.state.store
        ecdh = app.state.ecdh
        settings = app.state.settings
        # Resolve the BYOK key from the envelope store (no per-turn blob).
        dk = await _resolve_byok_from_envelope(
            request=_StubRequest(app=app),
            store=store,
            user_id=uid,
            family_id=None,
            key_handle="kh-turn",
        )
        assert dk is not None
        assert isinstance(dk, DecryptedKey)
        assert dk.provider_kind == "openai"
        assert dk.api_key_str() == "sk-byok-turn-1234567890"
        # build_chain with the envelope-resolved key puts the BYOK candidate first.
        cands = build_chain(
            enc_key_blob=None, settings=settings, ecdh=ecdh, model=None, byok_decrypted=dk
        )
        byok = cands[0]
        assert byok.kind == "openai"
        assert byok.decrypted is dk
        assert byok.decrypted.api_key_str() == "sk-byok-turn-1234567890"
        # Zeroize after the chain (the caller's contract).
        for i in range(len(dk.api_key)):
            dk.api_key[i] = 0


class _StubRequest:
    """Minimal stand-in for ``fastapi.Request`` carrying only ``app.state``."""

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self.app = app


async def test_per_turn_tampered_ciphertext_falls_back_no_500(make_app, app_client) -> None:
    """A tampered ``api_key_ciphertext`` raises ``EnvelopeDecryptError`` which
    the per-turn path catches → the turn degrades to env/mock (never 500)."""
    async with _client_ctx(make_app, app_client) as ac:
        uid = await _signup(ac, "tamper@x.com")
        # Insert a provider row with a TAMPERED ciphertext directly via the store.
        from ai_companion_contracts import Provider, ProviderKind

        store = ac._transport.app.state.store  # type: ignore[attr-defined]
        tampered = base64.b64encode(b"not-a-valid-envelope-ciphertext-payload!!").decode()
        p = Provider(
            id="p-tamper",
            user_id=uid,
            kind=ProviderKind.openai,
            label="Tampered",
            base_url=None,
            key_handle="kh-tamper",
            model=None,
            enc_blob=None,
        )
        await store.add_provider(p, api_key_ciphertext=tampered)

        # Drive a turn pointing at the tampered key_handle.
        async with ac.stream(
            "POST",
            "/v1/llm/stream",
            json={
                "persona_id": "sam",
                "convo_id": "c-tamper",
                "message": "hello",
                "enc_key_blob": None,
                "key_handle": "kh-tamper",
            },
        ) as resp:
            assert resp.status_code == 200
            events = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))
        types = [e["type"] for e in events]
        # No 500, no error event — the tampered ciphertext was caught and the
        # turn fell through to env/mock. The wire contract is intact.
        assert "error" not in types, events
        assert types[-1] == "done"


# --- cross-user scoping ------------------------------------------------------


async def test_cross_user_ciphertext_is_invisible(make_app, app_client) -> None:
    """User A's ``api_key_ciphertext`` is invisible to user B: the store lookup
    filters on ``user_id``, so B's per-turn resolution for A's key_handle
    returns None → env-fallback chain (no leak, no 500)."""
    async with _client_ctx(make_app, app_client) as ac_a:
        await _signup(ac_a, "alice@x.com")
        health = await ac_a.get("/v1/health")
        pub_b64 = health.json()["ecdh_pub"]
        blob = _seal_key(
            {"provider_kind": "openai", "api_key": "sk-alice-secret-1234567890", "base_url": None},
            pub_b64,
        )
        r = await ac_a.post(
            "/v1/providers",
            json={"kind": "openai", "label": "Alice", "key_handle": "kh-alice", "enc_key_blob": blob},
        )
        assert r.status_code == 200, r.text

    async with _client_ctx(make_app, app_client) as ac_b:
        await _signup(ac_b, "bob@x.com")
        # Bob cannot see Alice's provider row at all.
        rows = (await ac_b.get("/v1/providers")).json()
        assert all(p["key_handle"] != "kh-alice" for p in rows)
        # The store returns None for Bob querying Alice's key_handle.
        store = ac_b._transport.app.state.store  # type: ignore[attr-defined]
        ct = await store.get_provider_api_key_ciphertext(
            user_id="bob-does-not-exist", key_handle="kh-alice"
        )
        assert ct is None
        # Bob's turn with Alice's key_handle resolves to None → env/mock, no 500.
        async with ac_b.stream(
            "POST",
            "/v1/llm/stream",
            json={
                "persona_id": "sam",
                "convo_id": "c-bob",
                "message": "hello",
                "enc_key_blob": None,
                "key_handle": "kh-alice",
            },
        ) as resp:
            assert resp.status_code == 200
            events = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))
        types = [e["type"] for e in events]
        assert "error" not in types, events
        assert types[-1] == "done"


# --- unit: parse_decrypted_key + EnvelopeCipher round-trip ------------------


def test_envelope_round_trip_with_extras() -> None:
    """The envelope stores the full key JSON so provider extras (e.g. Bedrock's
    AWS triplet) survive the round-trip."""
    from ai_companion_api.crypto.envelope import EnvelopeCipher
    from ai_companion_api.vault.decrypt import parse_decrypted_key

    env = EnvelopeCipher.from_base64(EnvelopeCipher.generate_key_b64())
    payload = {
        "provider_kind": "bedrock",
        "api_key": "AKIA-test-access-key",
        "base_url": None,
        "extra": {"aws_secret_access_key": "secret", "aws_region_name": "us-east-1"},
    }
    ct = env.encrypt_b64(json.dumps(payload).encode("utf-8"))
    dk = parse_decrypted_key(env.decrypt_b64(ct))
    assert dk.provider_kind == "bedrock"
    assert dk.api_key_str() == "AKIA-test-access-key"
    assert dk.base_url is None
    assert dk.extra == {"aws_secret_access_key": "secret", "aws_region_name": "us-east-1"}


def test_envelope_decrypt_tampered_raises() -> None:
    from ai_companion_api.crypto.envelope import EnvelopeCipher, EnvelopeDecryptError

    env = EnvelopeCipher.from_base64(EnvelopeCipher.generate_key_b64())
    ct = env.encrypt_b64(b"some payload")
    # Flip one byte of the raw ciphertext (nonce||ct) — base64 still decodes but
    # Poly1305 MAC fails → EnvelopeDecryptError.
    raw = bytearray(base64.b64decode(ct))
    raw[-1] ^= 1
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(EnvelopeDecryptError):
        env.decrypt_b64(tampered)


# --- grep -r 'sk-' deploy/ must stay empty (CLAUDE.md security invariant) ----


def test_grep_sk_in_deploy_is_empty() -> None:
    """``grep -r 'sk-' deploy/`` must return nothing — ``api_key_ciphertext``
    is base64 ciphertext and the plaintext key is never logged."""
    import os
    import subprocess

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    deploy_dir = os.path.join(repo_root, "deploy")
    if not os.path.isdir(deploy_dir):
        pytest.skip("no deploy/ directory in this checkout")
    r = subprocess.run(
        ["git", "grep", "-n", "sk-"], cwd=repo_root, capture_output=True, text=True
    )
    # git grep returns 1 when there are no matches — that's the success case.
    # If git is unavailable or the repo isn't a git repo, fall back to ripgrep.
    if r.returncode == 128:
        # not a git repo here — use a plain recursive grep
        import shutil

        grep_bin = shutil.which("grep") or "grep"
        r2 = subprocess.run(
            [grep_bin, "-rn", "sk-", deploy_dir], capture_output=True, text=True
        )
        assert r2.stdout == "", f"found 'sk-' in deploy/:\n{r2.stdout}"
        return
    assert r.stdout == "", f"found 'sk-' in deploy/:\n{r.stdout}"