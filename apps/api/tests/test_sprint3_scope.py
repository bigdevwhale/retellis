"""Sprint 3 scope-correctness regressions (I1, I2, I7, I8, I12, I13).

These guard the invariants added in Sprint 3:

- **I1** — a provider that streams partial tokens then fails must NOT leave
  its partial in the persisted assistant text; the ``fallback`` event resets
  ``assistant_text`` so only the fallback provider's full reply is kept.
- **I2** — if post-turn work (judge/extract) throws AFTER ``done`` was sent,
  the wire contract stays ``… → done`` — never a second ``done`` or an
  ``error``-after-``done``.
- **I7** — concurrent ``append_event`` calls in the same convo serialize per
  ``(user_id, persona_id, convo_id)`` so the ``prev_event_id`` chain stays
  linear (no fork with two heads sharing a parent).
- **I8** — a retried turn with the same ``request_id`` dedups persistence by
  ``(user_id, convo_id, request_id)``: two stream calls with the same id
  persist one user+assistant pair, not two.
- **I12** — ``_normalize_family_scope`` defaults ``visibility`` to
  ``"private"`` when ``family_id`` is set but ``visibility`` is None (a
  no-op filter would otherwise mix personal + family rows).
- **I13** — ``POST /v1/journal`` threads ``family_id`` / ``visibility`` /
  ``participant_user_id`` onto the row so a family-scoped journal entry can
  be created (not just deleted).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from ai_companion_contracts import EventRole

import ai_companion_api.llm.provider as prov
from ai_companion_api.llm import LlmCallError, MockAdapter, RoutingCandidate
from ai_companion_api.llm.types import LlmAdapter, LlmUsage
from ai_companion_api.memory import InMemoryStore, append_event
from ai_companion_api.routers import llm as llm_router
from ai_companion_api.routers.memory import _normalize_family_scope


async def _drain(client, body: dict) -> list[dict]:
    """POST a stream, drain it fully (so post-done persist runs), return events."""
    events: list[dict] = []
    async with client.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def _force_mock() -> None:
    """Push the env-fallback off so the mock adapter serves regardless of shell env."""
    prov._env_key = lambda settings, kind: None  # noqa: E731


# --- I1: fallback resets assistant_text (no partial leak) --------------------


class _PartialFailingAdapter(LlmAdapter):
    """Yields a partial token THEN fails — exercises the fallback reset."""

    provider_kind = "openai"

    async def stream(self, messages, model) -> AsyncIterator[str]:  # noqa: ANN001
        yield "PARTIAL-LEAK-SHOULD-NOT-PERSIST"
        raise LlmCallError("provider died mid-stream")
        yield ""  # pragma: no cover  # make it an async generator

    def last_usage(self) -> LlmUsage:
        return LlmUsage("openai", "gpt-4o-mini", 5, 5, 0.0)


async def test_i01_fallback_drops_partial_before_persist(client, monkeypatch) -> None:
    real_env_key = prov._env_key
    _force_mock()

    def fake_build_chain(*, enc_key_blob, settings, ecdh, model=None, byok_decrypted=None):  # noqa: ANN001, ARG001
        return [
            RoutingCandidate(
                kind="openai",
                model="gpt-4o-mini",
                base_url=None,
                adapter=_PartialFailingAdapter(),
                is_mock=False,
                decrypted=None,
            ),
            RoutingCandidate(
                kind="mock",
                model="mock",
                base_url=None,
                adapter=MockAdapter(),
                is_mock=True,
                decrypted=None,
            ),
        ]

    monkeypatch.setattr(llm_router, "build_chain", fake_build_chain)
    try:
        events = await _drain(
            client, {"persona_id": "aria", "convo_id": "c-i1", "message": "rough day"}
        )
    finally:
        prov._env_key = real_env_key

    types = _types(events)
    assert "fallback" in types
    # The partial was streamed, THEN a fallback fired, THEN mock tokens came.
    fb_idx = types.index("fallback")
    assert any(
        e["type"] == "token" and e["text"] == "PARTIAL-LEAK-SHOULD-NOT-PERSIST"
        for e in events[:fb_idx]
    )
    # The persisted assistant row must be the mock reply only — no partial leak.
    r = await client.get("/v1/memory", params={"persona_id": "aria", "convo_id": "c-i1"})
    rows = r.json()
    assistant = [e for e in rows if e["role"] == "assistant"]
    assert assistant, "expected the assistant event to be persisted"
    assert "PARTIAL-LEAK-SHOULD-NOT-PERSIST" not in assistant[0]["content"]
    assert "sk-" not in json.dumps(rows)


# --- I2: no double done / no error-after-done when post-turn work throws -----


async def test_i02_no_double_done_when_post_turn_work_throws(client, monkeypatch) -> None:
    real_env_key = prov._env_key
    _force_mock()

    async def boom(*args, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("judge LLM exploded")

    monkeypatch.setattr(llm_router, "_post_turn_work", boom)
    try:
        events = await _drain(
            client, {"persona_id": "aria", "convo_id": "c-i2", "message": "hi there"}
        )
    finally:
        prov._env_key = real_env_key

    types = _types(events)
    # Exactly one done, and no error event sneaks in after it.
    assert types.count("done") == 1
    assert "error" not in types
    assert types[-1] == "done"


# --- I7: per-convo append_event serialization (chain stays linear) -----------


@pytest.mark.asyncio
async def test_i07_concurrent_append_does_not_fork_chain() -> None:
    store = InMemoryStore()
    # Fire several appends in the SAME convo concurrently. Without per-convo
    # serialization each would read the same prev_event_id and fork; with the
    # lock they serialize into one linear chain.
    convo = "convo-race"
    new_events = await asyncio.gather(
        *(
            append_event(
                store,
                user_id="u1",
                persona_id="aria",
                convo_id=convo,
                role=EventRole.user,
                content=f"msg-{i}",
            )
            for i in range(6)
        )
    )
    rows = await store.list_events(user_id="u1", persona_id="aria", convo_id=convo)
    assert len(rows) == 6
    # A linear chain has exactly ONE head (prev_event_id is None) and every
    # other event links to a unique predecessor present in the set.
    ids = {e.id for e in rows}
    heads = [e for e in rows if e.prev_event_id is None]
    assert len(heads) == 1, "concurrent appends forked the chain (more than one head)"
    for e in rows:
        if e.prev_event_id is not None:
            assert e.prev_event_id in ids, "prev_event_id points outside the convo"
    # The gathered return order is completion order; the chain must still be a
    # single linked list (each non-head event's prev is some other event).
    prevs = [e.prev_event_id for e in rows if e.prev_event_id is not None]
    assert len(set(prevs)) == len(prevs), "two events share a parent → fork"
    # All six appended events are present.
    assert {e.id for e in new_events} == ids


# --- I8: request_id idempotency dedups persistence ---------------------------


async def test_i08_retry_with_same_request_id_does_not_duplicate(client) -> None:
    real_env_key = prov._env_key
    _force_mock()
    try:
        await _drain(
            client,
            {
                "persona_id": "aria",
                "convo_id": "c-i8",
                "message": "remember this turn",
                "request_id": "req-stable-1",
            },
        )
        # A "retry" with the SAME request_id re-streams (fresh reply) but the
        # server dedups persistence — no second user+assistant pair, no fork.
        await _drain(
            client,
            {
                "persona_id": "aria",
                "convo_id": "c-i8",
                "message": "remember this turn",
                "request_id": "req-stable-1",
            },
        )
    finally:
        prov._env_key = real_env_key

    r = await client.get("/v1/memory", params={"persona_id": "aria", "convo_id": "c-i8"})
    rows = r.json()
    # Exactly one user + one assistant event — the retry did not duplicate.
    assert len(rows) == 2
    assert sum(1 for e in rows if e["role"] == "user") == 1
    assert sum(1 for e in rows if e["role"] == "assistant") == 1
    # Chain stays linear: the assistant links to the user event.
    user_ev = next(e for e in rows if e["role"] == "user")
    asst_ev = next(e for e in rows if e["role"] == "assistant")
    assert asst_ev["prev_event_id"] == user_ev["id"]


async def test_i08_distinct_request_ids_both_persist(client) -> None:
    """Control: two turns with DIFFERENT request_ids both persist (no false dedup)."""
    real_env_key = prov._env_key
    _force_mock()
    try:
        await _drain(
            client,
            {"persona_id": "aria", "convo_id": "c-i8b", "message": "first", "request_id": "r1"},
        )
        await _drain(
            client,
            {"persona_id": "aria", "convo_id": "c-i8b", "message": "second", "request_id": "r2"},
        )
    finally:
        prov._env_key = real_env_key

    r = await client.get("/v1/memory", params={"persona_id": "aria", "convo_id": "c-i8b"})
    rows = r.json()
    # Two turns → two user + two assistant events.
    assert len(rows) == 4
    assert sum(1 for e in rows if e["role"] == "user") == 2


def test_i08_idem_done_is_fifo_bounded() -> None:
    """``_idem_done`` must not grow without bound: oldest keys are evicted
    once the cap is reached (a retry arrives within minutes; a key evicted
    thousands of turns later has no dedup value left)."""
    from ai_companion_api.routers import llm as llm_router

    saved = dict(llm_router._idem_done)
    llm_router._idem_done.clear()
    try:
        for i in range(llm_router._IDEM_DONE_MAX + 100):
            llm_router._idem_mark_done(("u", "c", f"r{i}"))
        assert len(llm_router._idem_done) == llm_router._IDEM_DONE_MAX
        assert ("u", "c", "r0") not in llm_router._idem_done
        newest = ("u", "c", f"r{llm_router._IDEM_DONE_MAX + 99}")
        assert newest in llm_router._idem_done
    finally:
        llm_router._idem_done.clear()
        llm_router._idem_done.update(saved)


# --- I12: _normalize_family_scope defaults visibility to private -------------


def test_i12_normalize_defaults_visibility_to_private() -> None:
    # family_id set + visibility None → private (the solo predicate), so
    # GET /v1/memory?family_id=F does not mix personal + family rows.
    fam, vis = _normalize_family_scope("fam-1", None)
    assert (fam, vis) == ("fam-1", "private")


def test_i12_normalize_no_op_without_family_id() -> None:
    # No family_id → no scope filter at all (personal path).
    fam, vis = _normalize_family_scope(None, None)
    assert (fam, vis) == (None, None)


def test_i12_normalize_preserves_explicit_visibility() -> None:
    # An explicit shared/private is kept as-is (not overwritten).
    assert _normalize_family_scope("fam-1", "shared") == ("fam-1", "shared")
    assert _normalize_family_scope("fam-1", "private") == ("fam-1", "private")


# --- I13: POST /v1/journal threads family scope onto the row -----------------


async def test_i13_journal_create_with_family_scope(make_app, app_client) -> None:
    # M1.3: POST /v1/journal now requires the caller's Principal to actually be
    # in the family named by ``family_id`` (404 on mismatch, like /llm/stream).
    # Use the real auth path: signup → create family (sets users.family_id) →
    # post a family-scoped journal entry with that family_id.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with app_client(app) as ac:
            yield ac

    async with _ctx() as ac:
        await ac.post("/v1/auth/signup", json={"email": "owner@x.com", "password": "pwaaaaaaaaaa"})
        fam = (await ac.post("/v1/family", json={"name": "Cohort"})).json()
        me = (await ac.get("/v1/auth/me")).json()
        r = await ac.post(
            "/v1/journal",
            json={
                "persona_id": "fam",
                "body": "Family session reflection.",
                "title": "Today",
                "mood": "calm",
                "tags": ["family"],
                "salience": 0.6,
                "family_id": fam["id"],
                "visibility": "private",
                "participant_user_id": me["user_id"],
            },
        )
        assert r.status_code == 200, r.text
        entry = r.json()
        assert entry["family_id"] == fam["id"]
        assert entry["visibility"] == "private"
        assert entry["participant_user_id"] == me["user_id"]
        # mood/tags are surfaced AS AUTHORED, never generated.
        assert entry["mood"] == "calm"
        assert entry["tags"] == ["family"]
        # No key material on the journal surface.
        assert "sk-" not in json.dumps(entry)


async def test_i13_journal_create_rejects_family_not_on_principal(client) -> None:
    """M1.3: a family_id that the Principal is NOT in → 404 (not 403), matching
    the cross-tenant convention and /llm/stream. The default client Principal
    has no family, so naming any family_id must be rejected."""
    r = await client.post(
        "/v1/journal",
        json={
            "persona_id": "fam",
            "body": "Reflection.",
            "family_id": "fam-not-mine",
        },
    )
    assert r.status_code == 404


async def test_i13_journal_create_defaults_to_personal(client) -> None:
    """Omitting family_id writes a personal (non-family) entry — the default
    the /journal page uses today; family_id stays null on the row."""
    r = await client.post(
        "/v1/journal",
        json={"persona_id": "aria", "body": "Personal diary entry.", "tags": []},
    )
    assert r.status_code == 200
    entry = r.json()
    assert entry["family_id"] is None
    assert entry["visibility"] == "private"
