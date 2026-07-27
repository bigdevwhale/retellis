"""MessengerStore: in-memory CRUD round-trip + cross-user isolation + factory."""

from __future__ import annotations

import pytest

from ai_companion_api.messengers.store import (
    InMemoryMessengerStore,
    MessengerRecord,
    make_messenger_store,
)


@pytest.fixture
def store() -> InMemoryMessengerStore:
    return InMemoryMessengerStore()


async def test_create_get_roundtrip(store: InMemoryMessengerStore) -> None:
    m = await store.create(
        user_id="u1",
        kind="telegram",
        bot_token_ciphertext="ct-aaa",
        bot_token_masked="…XYZW",
        persona_id="p1",
    )
    assert m.id
    assert m.status == "pending_handshake"
    assert m.user_id == "u1"
    assert m.bot_token_ciphertext == "ct-aaa"
    assert m.bot_token_masked == "…XYZW"
    assert m.next_offset == 0
    assert m.byok_enc_blob is None

    fetched = await store.get(m.id)
    assert fetched is not None
    assert fetched.id == m.id


async def test_get_for_user_isolation(store: InMemoryMessengerStore) -> None:
    m = await store.create(
        user_id="u1", kind="telegram", bot_token_ciphertext="ct",
        bot_token_masked="…X", persona_id="p1",
    )
    # Owner sees it.
    assert await store.get_for_user(m.id, "u1") is not None
    # A different user does not — cross-scope looks like missing (404 contract).
    assert await store.get_for_user(m.id, "u2") is None
    # Raw get still returns it (used internally by the poller, not by routers).
    assert await store.get(m.id) is not None


async def test_list_by_user_and_active(store: InMemoryMessengerStore) -> None:
    m1 = await store.create(
        user_id="u1", kind="telegram", bot_token_ciphertext="ct1",
        bot_token_masked="…1", persona_id="p1",
    )
    await store.create(
        user_id="u2", kind="telegram", bot_token_ciphertext="ct2",
        bot_token_masked="…2", persona_id="p2",
    )
    only_u1 = await store.list_by_user("u1")
    assert [m.id for m in only_u1] == [m1.id]

    # Nothing active yet.
    assert await store.list_active() == []
    await store.update(m1.id, status="active")
    active = await store.list_active()
    assert [m.id for m in active] == [m1.id]


async def test_create_is_idempotent_per_user_kind(store: InMemoryMessengerStore) -> None:
    """One bot per (user, kind) — a double-clicked init must not duplicate."""
    a = await store.create(
        user_id="u1", kind="telegram", bot_token_ciphertext="ct",
        bot_token_masked="…X", persona_id="p1",
    )
    b = await store.create(
        user_id="u1", kind="telegram", bot_token_ciphertext="ct-different",
        bot_token_masked="…Y", persona_id="p2",
    )
    assert a.id == b.id
    assert len(await store.list_by_user("u1")) == 1


async def test_update_fields_and_timestamp(store: InMemoryMessengerStore) -> None:
    m = await store.create(
        user_id="u1", kind="telegram", bot_token_ciphertext="ct",
        bot_token_masked="…X", persona_id="p1",
    )
    before = m.updated_at
    updated = await store.update(
        m.id, status="active", persona_id="p9", chat_id=4242, next_offset=99,
    )
    assert updated is not None
    assert updated.status == "active"
    assert updated.persona_id == "p9"
    assert updated.chat_id == 4242
    assert updated.next_offset == 99
    assert updated.updated_at is not None
    assert updated.updated_at >= before  # type: ignore[operator]


async def test_update_rejects_unknown_field(store: InMemoryMessengerStore) -> None:
    m = await store.create(
        user_id="u1", kind="telegram", bot_token_ciphertext="ct",
        bot_token_masked="…X", persona_id="p1",
    )
    with pytest.raises(ValueError, match="not updatable"):
        await store.update(m.id, id="hacker", user_id="attacker")  # id/user_id are not in _UPDATABLE


async def test_update_missing_returns_none(store: InMemoryMessengerStore) -> None:
    assert await store.update("nope", status="active") is None


async def test_delete(store: InMemoryMessengerStore) -> None:
    m = await store.create(
        user_id="u1", kind="telegram", bot_token_ciphertext="ct",
        bot_token_masked="…X", persona_id="p1",
    )
    assert await store.delete(m.id) is True
    assert await store.get(m.id) is None
    # Idempotent delete (204 contract).
    assert await store.delete(m.id) is False


def test_make_messenger_store_picks_in_memory_by_default() -> None:
    from ai_companion_api.config import Settings

    s = Settings()
    assert s.use_db is False
    store = make_messenger_store(s)
    assert isinstance(store, InMemoryMessengerStore)


def test_messenger_record_is_a_dataclass() -> None:
    # Sanity: ensure the record type is constructible without secrets leaking
    # through default repr — the ciphertext field is plain to read but never
    # holds plaintext.
    r = MessengerRecord(
        id="x", user_id="u", kind="telegram", status="pending_handshake",
        persona_id="p", bot_token_ciphertext="ct", bot_token_masked="…X",
    )
    assert "ct" in repr(r)  # ciphertext is fine to repr; plaintext is never here