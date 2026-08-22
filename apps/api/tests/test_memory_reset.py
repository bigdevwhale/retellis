"""``DELETE /v1/memory/convo`` + ``DELETE /v1/memory`` — dialog + persona reset.

Feature B (per-conversation delete) removes one thread's raw message events
server-side; derived memories persist. Feature A (persona wipe) un-learns a
persona's events + memories + its OUTGOING donor shares; incoming shares from
other personas are donor-owned and stay. Both are idempotent (204 on missing).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_companion_contracts import Memory, MemoryStatus

import ai_companion_api.llm.provider as prov
from ai_companion_api.memory import InMemoryStore, append_event

USER = "u1"


async def _stream_mock(client, body: dict) -> None:
    """POST a stream and drain it so the persist-after-done side effect runs."""
    async with client.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass


def _inject_fake_adapter():
    """Inject a fake adapter for testing without requiring real API keys."""
    from ai_companion_api.llm import RoutingCandidate
    from ai_companion_api.llm.types import LlmAdapter, LlmUsage
    from ai_companion_api.routers import llm as llm_router

    class _FakeAdapter(LlmAdapter):
        provider_kind = "test"

        async def stream(self, messages, model):
            yield "test reply"
            yield ""  # pragma: no cover

        def last_usage(self):
            return LlmUsage("test", "test-model", 2, 1, 0.0)

    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731

    # Also inject a fake build_chain that returns our fake adapter
    original_build_chain = llm_router.build_chain

    def fake_build_chain(*, enc_key_blob, settings, ecdh, model=None, byok_decrypted=None):
        return [RoutingCandidate(
            kind="test",
            model="test-model",
            base_url=None,
            adapter=_FakeAdapter(),
            is_mock=False,
            decrypted=None,
        )]

    llm_router.build_chain = fake_build_chain
    return real, original_build_chain


# --- Feature B: per-conversation server delete -------------------------------


async def test_delete_convo_removes_only_that_threads_events(client) -> None:
    from ai_companion_api.routers import llm as llm_router

    real_env_key, original_build_chain = _inject_fake_adapter()
    try:
        await _stream_mock(
            client, {"persona_id": "aria", "convo_id": "c1", "message": "My dog Maple died."}
        )
        await _stream_mock(
            client, {"persona_id": "aria", "convo_id": "c2", "message": "I got a job at Acme."}
        )
    finally:
        prov._env_key = real_env_key
        llm_router.build_chain = original_build_chain

    # Both threads present (2 events each: user + assistant).
    events = (await client.get("/v1/memory", params={"persona_id": "aria"})).json()
    assert len(events) == 4

    r = await client.delete("/v1/memory/convo", params={"persona_id": "aria", "convo_id": "c1"})
    assert r.status_code == 204

    events = (await client.get("/v1/memory", params={"persona_id": "aria"})).json()
    assert len(events) == 2  # only c2's events remain
    contents = " ".join(e["content"] for e in events)
    assert "Acme" in contents
    assert "Maple" not in contents  # c1's messages are gone server-side


async def test_delete_convo_then_recall_forgets_that_thread(client) -> None:
    from ai_companion_api.routers import llm as llm_router

    real_env_key, original_build_chain = _inject_fake_adapter()
    try:
        await _stream_mock(
            client, {"persona_id": "aria", "convo_id": "c1", "message": "My dog Maple died."}
        )
    finally:
        prov._env_key = real_env_key
        llm_router.build_chain = original_build_chain

    # Before delete, recall surfaces Maple.
    before = (
        await client.post(
            "/v1/memory/recall",
            json={"persona_id": "aria", "query": "What was the name of my dog?"},
        )
    ).json()
    assert before and "Maple" in " ".join(e["content"] for ch in before for e in ch["events"])

    await client.delete("/v1/memory/convo", params={"persona_id": "aria", "convo_id": "c1"})

    after = (
        await client.post(
            "/v1/memory/recall",
            json={"persona_id": "aria", "query": "What was the name of my dog?"},
        )
    ).json()
    # The companion no longer "remembers" the deleted thread.
    assert after == [] or "Maple" not in " ".join(
        e["content"] for ch in after for e in ch["events"]
    )


# --- Feature A: persona memory wipe ------------------------------------------


async def _seed_aria_with_shares(client) -> None:
    from ai_companion_api.routers import llm as llm_router

    real_env_key, original_build_chain = _inject_fake_adapter()
    try:
        await _stream_mock(
            client, {"persona_id": "aria", "convo_id": "c1", "message": "My dog Maple died."}
        )
    finally:
        prov._env_key = real_env_key
        llm_router.build_chain = original_build_chain
    # Outgoing: aria shares INTO sam.
    r = await client.post(
        "/v1/memory/shares",
        json={"donor_persona_id": "aria", "receiver_persona_id": "sam"},
    )
    assert r.status_code == 200
    # Incoming: sam shares INTO aria (donor-owned by sam — must survive aria's wipe).
    r = await client.post(
        "/v1/memory/shares",
        json={"donor_persona_id": "sam", "receiver_persona_id": "aria"},
    )
    assert r.status_code == 200


async def test_wipe_persona_unlearns_events_and_outgoing_shares(client) -> None:
    await _seed_aria_with_shares(client)

    assert (await client.get("/v1/memory", params={"persona_id": "aria"})).json()  # non-empty
    assert (await client.get("/v1/memory/shares", params={"donor_persona_id": "aria"})).json()

    r = await client.delete("/v1/memory", params={"persona_id": "aria"})
    assert r.status_code == 204

    # Events + memories gone.
    assert (await client.get("/v1/memory", params={"persona_id": "aria"})).json() == []
    assert (await client.get("/v1/memories", params={"persona_id": "aria"})).json() == []
    # Outgoing donor shares gone — aria no longer shares into anyone.
    assert (await client.get("/v1/memory/shares", params={"donor_persona_id": "aria"})).json() == []


async def test_wipe_persona_keeps_incoming_shares_from_others(client) -> None:
    await _seed_aria_with_shares(client)

    await client.delete("/v1/memory", params={"persona_id": "aria"})

    # The sam → aria link is sam's OUTGOING share, donor-owned by sam — wiping
    # the receiver (aria) must NOT revoke it. sam still lists aria as a receiver.
    sam_shares = (await client.get("/v1/memory/shares", params={"donor_persona_id": "sam"})).json()
    assert any(s["receiver_persona_id"] == "aria" for s in sam_shares)


# --- idempotent edge ---------------------------------------------------------


async def test_delete_convo_missing_returns_204(client) -> None:
    r = await client.delete(
        "/v1/memory/convo", params={"persona_id": "aria", "convo_id": "never-existed"}
    )
    assert r.status_code == 204


async def test_wipe_persona_missing_returns_204(client) -> None:
    r = await client.delete("/v1/memory", params={"persona_id": "ghost"})
    assert r.status_code == 204


# --- store unit (InMemoryStore) — scope rules, no HTTP -----------------------


async def test_store_delete_convo_events_counts_and_scopes() -> None:
    store = InMemoryStore()
    # aria c1: two events. aria c2: one event. sam c1: one event.
    await append_event(
        store,
        user_id=USER,
        persona_id="aria",
        convo_id="c1",
        role="user",
        content="Maple died",
    )
    await append_event(
        store,
        user_id=USER,
        persona_id="aria",
        convo_id="c1",
        role="assistant",
        content="I'm sorry",
    )
    await append_event(
        store,
        user_id=USER,
        persona_id="aria",
        convo_id="c2",
        role="user",
        content="Got a job at Acme",
    )
    await append_event(
        store,
        user_id=USER,
        persona_id="sam",
        convo_id="c1",
        role="user",
        content="Rough day",
    )

    removed = await store.delete_convo_events(user_id=USER, persona_id="aria", convo_id="c1")
    assert removed == 2  # only aria/c1's two events, not aria/c2 or sam/c1

    aria_events = await store.list_events(user_id=USER, persona_id="aria")
    assert len(aria_events) == 1
    assert aria_events[0].content == "Got a job at Acme"
    sam_events = await store.list_events(user_id=USER, persona_id="sam")
    assert len(sam_events) == 1  # other persona untouched


async def test_store_wipe_persona_drops_outgoing_keeps_incoming() -> None:
    store = InMemoryStore()
    await append_event(
        store,
        user_id=USER,
        persona_id="aria",
        convo_id="c1",
        role="user",
        content="aria fact",
    )
    await append_event(
        store,
        user_id=USER,
        persona_id="sam",
        convo_id="c1",
        role="user",
        content="sam fact",
    )
    now = datetime.now(UTC)
    await store.add_memory(
        Memory(
            id="m-aria",
            user_id=USER,
            persona_id="aria",
            content="aria knows X",
            tags=[],
            salience=0.5,
            source_event_ids=[],
            status=MemoryStatus.active,
            created_at=now,
            updated_at=now,
        )
    )
    await store.add_memory(
        Memory(
            id="m-sam",
            user_id=USER,
            persona_id="sam",
            content="sam knows Y",
            tags=[],
            salience=0.5,
            source_event_ids=[],
            status=MemoryStatus.active,
            created_at=now,
            updated_at=now,
        )
    )
    # Outgoing from aria; incoming to aria (donor = sam).
    await store.add_share(user_id=USER, donor_persona_id="aria", receiver_persona_id="sam")
    await store.add_share(user_id=USER, donor_persona_id="sam", receiver_persona_id="aria")

    await store.wipe_persona_memory(user_id=USER, persona_id="aria")

    # aria's events + memories gone; sam's untouched.
    assert await store.list_events(user_id=USER, persona_id="aria") == []
    assert await store.list_memories(user_id=USER, persona_id="aria", include_donors=False) == []
    assert len(await store.list_events(user_id=USER, persona_id="sam")) == 1
    assert len(await store.list_memories(user_id=USER, persona_id="sam", include_donors=False)) == 1
    # aria's outgoing share gone; the sam→aria link (sam's outgoing) survives.
    assert await store.list_shares(user_id=USER, donor_persona_id="aria") == []
    sam_out = await store.list_shares(user_id=USER, donor_persona_id="sam")
    assert any(s.receiver_persona_id == "aria" for s in sam_out)
