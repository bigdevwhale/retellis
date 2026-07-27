"""Family ``use_owner_personal_key`` toggle — owner-only PUT + the
per-turn BYOK resolution branch it selects.

The flag (``families.use_owner_personal_key``, migration 0024) lets the
family owner share their *active personal* BYOK key with the whole family
instead of entering a separate family key. On a family turn, the server's
``_resolve_byok_from_envelope`` reads the flag off the family record and
switches the ciphertext lookup from ``family_providers`` to the owner's
personal ``providers`` row — keyed by ``family.owner_user_id`` (never a
client value, so a member cannot retarget the lookup).

These tests pin:
  - the owner-only ``PUT /v1/family/owner-personal-key`` (on/off + GET echo)
  - a stranger (no family) gets 404, not 403 (cross-family contract)
  - the resolution branch: flag-on → personal store called with
    ``owner_user_id``; flag-off → family store called; ``key_handle=None``
    short-circuits to ``None`` (no 500); the owner is resolved from the
    family record, never the request ``user_id``.
  - the security invariant: no ``sk-`` substring in any toggle response.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ai_companion_contracts.models import Family

from ai_companion_api.routers.llm import _resolve_byok_from_envelope


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


# --- PUT owner-only + GET echo -------------------------------------------


async def test_owner_can_toggle_use_owner_personal_key(make_app, app_client) -> None:
    """Owner PUTs the flag on, then off; GET /v1/family echoes it both ways."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        fam = await _new_family(ac)
        # Fresh families default the flag off.
        assert fam["use_owner_personal_key"] is False

        r = await ac.put("/v1/family/owner-personal-key", json={"use_owner_personal_key": True})
        assert r.status_code == 200, r.text
        on = r.json()
        assert on["use_owner_personal_key"] is True
        # GET /v1/family echoes the persisted flag back to all members.
        r2 = await ac.get("/v1/family")
        assert r2.status_code == 200, r2.text
        assert r2.json()["family"]["use_owner_personal_key"] is True

        r = await ac.put("/v1/family/owner-personal-key", json={"use_owner_personal_key": False})
        assert r.status_code == 200, r.text
        assert r.json()["use_owner_personal_key"] is False
        r2 = await ac.get("/v1/family")
        assert r2.json()["family"]["use_owner_personal_key"] is False


async def test_use_owner_personal_key_rejects_missing_field(make_app, app_client) -> None:
    """The body requires ``use_owner_personal_key`` — an empty body is 422."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.put("/v1/family/owner-personal-key", json={})
        assert r.status_code == 422, r.text


async def test_use_owner_personal_key_non_member_404(make_app, app_client) -> None:
    """A stranger (never in a family) gets 404, not 403 — the cross-family
    contract: ``_require_owner`` → ``_require_member`` 404s before the 403
    owner check can run (mirrors ``therapist-prompt``)."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
    async with _new_client(make_app, app_client) as ac_stranger:
        await _signup(ac_stranger, "stranger@x.com")
        r = await ac_stranger.put(
            "/v1/family/owner-personal-key", json={"use_owner_personal_key": True}
        )
        assert r.status_code == 404, r.text


async def test_toggle_response_no_sk_leak(make_app, app_client) -> None:
    """The toggle response carries only the boolean — no key material."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.put("/v1/family/owner-personal-key", json={"use_owner_personal_key": True})
        assert "sk-" not in r.text
        r2 = await ac.get("/v1/family")
        assert "sk-" not in r2.text


# --- _resolve_byok_from_envelope branch -----------------------------------


def _fake_request(envelope, family_store) -> SimpleNamespace:
    """A minimal Request stand-in: ``_resolve_byok_from_envelope`` only reads
    ``request.app.state.envelope`` and ``request.app.state.family_store``."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        envelope=envelope, family_store=family_store
    )))


def _key_payload_json() -> bytes:
    """The plaintext JSON the envelope stores (same shape the ECDH blob
    carried). ``parse_decrypted_key`` consumes it after ``decrypt_b64``."""
    return json.dumps(
        {"provider_kind": "openai", "api_key": "fake-key-not-real", "base_url": None}
    ).encode("utf-8")


async def test_resolve_uses_owner_personal_key_when_flag_on() -> None:
    """Flag on: the family record's ``owner_user_id`` is used to look the key
    up in the OWNER's personal ``providers`` row. The request ``user_id`` (a
    member) is NOT used for the lookup — a member cannot retarget the key."""
    fam = Family(
        id="fam-1",
        name="Cohort",
        owner_user_id="u-owner",
        created_at="2026-07-24T00:00:00Z",  # noqa: DTZ001 — test fixture
        use_owner_personal_key=True,
    )
    family_store = SimpleNamespace(
        get_family=AsyncMock(return_value=fam),
        get_family_provider_api_key_ciphertext=AsyncMock(return_value=None),
    )
    # The personal store: assert it's called with the OWNER's user_id, not the
    # member's. Returns a sentinel ciphertext the envelope "decrypts".
    store = SimpleNamespace(
        get_provider_api_key_ciphertext=AsyncMock(return_value="ZmFrZS1jaXBoZXJ0ZXh0"),
    )
    envelope = SimpleNamespace(decrypt_b64=Mock(return_value=_key_payload_json()))

    dk = await _resolve_byok_from_envelope(
        request=_fake_request(envelope, family_store),
        store=store,  # type: ignore[arg-type]
        user_id="u-member",
        family_id="fam-1",
        key_handle="kh-owner-personal",
    )
    # The personal-store lookup used the owner's user_id, not the member's.
    store.get_provider_api_key_ciphertext.assert_awaited_once_with(
        user_id="u-owner", key_handle="kh-owner-personal"
    )
    # The family-store ciphertext path was NOT taken this turn.
    family_store.get_family_provider_api_key_ciphertext.assert_not_called()
    # A DecryptedKey came back with the parsed api_key.
    assert dk is not None
    assert dk.api_key_str() == "fake-key-not-real"
    assert dk.provider_kind == "openai"


async def test_resolve_uses_family_store_when_flag_off() -> None:
    """Flag off (the default): the family store's ciphertext path is used
    (same as before the toggle existed); the personal store is not."""
    fam = Family(
        id="fam-1",
        name="Cohort",
        owner_user_id="u-owner",
        created_at="2026-07-24T00:00:00Z",  # noqa: DTZ001 — test fixture
        use_owner_personal_key=False,
    )
    family_store = SimpleNamespace(
        get_family=AsyncMock(return_value=fam),
        get_family_provider_api_key_ciphertext=AsyncMock(return_value="ZmFrZS1jaXBoZXJ0ZXh0"),
    )
    store = SimpleNamespace(
        get_provider_api_key_ciphertext=AsyncMock(return_value=None),
    )
    envelope = SimpleNamespace(decrypt_b64=Mock(return_value=_key_payload_json()))

    dk = await _resolve_byok_from_envelope(
        request=_fake_request(envelope, family_store),
        store=store,  # type: ignore[arg-type]
        user_id="u-owner",
        family_id="fam-1",
        key_handle="fam-kh-1",
    )
    family_store.get_family_provider_api_key_ciphertext.assert_awaited_once_with(
        family_id="fam-1", key_handle="fam-kh-1"
    )
    store.get_provider_api_key_ciphertext.assert_not_called()
    assert dk is not None
    assert dk.api_key_str() == "fake-key-not-real"


async def test_resolve_none_key_handle_returns_none() -> None:
    """No key_handle → no lookup at all (None). The chain falls through to the
    env ladder / mock — never a 500. No store method is called."""
    family_store = SimpleNamespace(
        get_family=AsyncMock(return_value=None),
        get_family_provider_api_key_ciphertext=AsyncMock(return_value=None),
    )
    store = SimpleNamespace(get_provider_api_key_ciphertext=AsyncMock(return_value=None))
    envelope = SimpleNamespace(decrypt_b64=Mock(return_value=b""))

    dk = await _resolve_byok_from_envelope(
        request=_fake_request(envelope, family_store),
        store=store,  # type: ignore[arg-type]
        user_id="u-member",
        family_id="fam-1",
        key_handle=None,
    )
    assert dk is None
    store.get_provider_api_key_ciphertext.assert_not_called()
    family_store.get_family_provider_api_key_ciphertext.assert_not_called()
    envelope.decrypt_b64.assert_not_called()


async def test_resolve_missing_ciphertext_returns_none() -> None:
    """Flag on but the owner has no provider row for that handle (wrong handle
    / owner wiped their key) → the personal store returns None → None (no
    500, no leak). The chain degrades to env/mock fallback."""
    fam = Family(
        id="fam-1",
        name="Cohort",
        owner_user_id="u-owner",
        created_at="2026-07-24T00:00:00Z",  # noqa: DTZ001 — test fixture
        use_owner_personal_key=True,
    )
    family_store = SimpleNamespace(
        get_family=AsyncMock(return_value=fam),
        get_family_provider_api_key_ciphertext=AsyncMock(return_value=None),
    )
    store = SimpleNamespace(get_provider_api_key_ciphertext=AsyncMock(return_value=None))
    envelope = SimpleNamespace(decrypt_b64=Mock(return_value=b""))

    dk = await _resolve_byok_from_envelope(
        request=_fake_request(envelope, family_store),
        store=store,  # type: ignore[arg-type]
        user_id="u-member",
        family_id="fam-1",
        key_handle="kh-not-yours",
    )
    assert dk is None
    # decrypt_b64 is never reached when the ciphertext is None — no work done.
    envelope.decrypt_b64.assert_not_called()