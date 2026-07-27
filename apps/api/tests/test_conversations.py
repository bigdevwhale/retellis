"""K6: ``GET /v1/conversations`` — the conversation-list projection derived
from the event chain.

Exercises: title (first user message, truncated), preview (last event),
``event_count``, ``last_activity`` desc ordering, cursor pagination via
``before``, persona filter, and cross-user isolation. Runs against the
in-memory store default. No key material; mock-only streams.
"""

from __future__ import annotations

import ai_companion_api.llm.provider as prov


def _headers(user: str) -> dict[str, str]:
    return {"X-User-Id": user}


async def _stream_mock(client, body: dict, user: str = "u1") -> None:
    """POST a stream and drain it so the persist-after-done side effect runs."""
    async with client.stream("POST", "/v1/llm/stream", json=body, headers=_headers(user)) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass


async def test_conversations_lists_two_convos_ordered_desc(client) -> None:
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731  force mock adapter
    try:
        await _stream_mock(
            client,
            {"persona_id": "aria", "convo_id": "c-week", "message": "I had a heavy week at work."},
        )
        await _stream_mock(
            client,
            {"persona_id": "aria", "convo_id": "c-hello", "message": "quick hello"},
        )
    finally:
        prov._env_key = real

    r = await client.get("/v1/conversations", headers=_headers("u1"))
    assert r.status_code == 200
    convos = r.json()
    assert len(convos) == 2
    # Most-recent first: c-hello was streamed last → its last_activity is later.
    assert convos[0]["convo_id"] == "c-hello"
    assert convos[1]["convo_id"] == "c-week"
    # Title is the first user message; preview is the last event (mock reply).
    assert convos[1]["title"] == "I had a heavy week at work."
    assert convos[1]["event_count"] == 2
    assert convos[1]["persona_id"] == "aria"
    assert convos[1]["preview"]  # non-empty assistant reply
    # last_activity is an ISO string and >= created_at.
    assert convos[0]["last_activity"] >= convos[0]["created_at"]
    assert convos[1]["last_activity"] >= convos[1]["created_at"]
    # No key material in the projection.
    assert "sk-" not in r.text


async def test_title_truncates_long_first_message(client) -> None:
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731
    long_msg = "x" * 200
    try:
        await _stream_mock(
            client,
            {"persona_id": "aria", "convo_id": "c-long", "message": long_msg},
        )
    finally:
        prov._env_key = real
    convos = (await client.get("/v1/conversations", headers=_headers("u1"))).json()
    assert len(convos) == 1
    assert len(convos[0]["title"]) <= 60
    assert convos[0]["title"].endswith("…")


async def test_persona_filter_scopes_to_one_persona(client) -> None:
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731
    try:
        await _stream_mock(client, {"persona_id": "aria", "convo_id": "c-a", "message": "hi aria"})
        await _stream_mock(client, {"persona_id": "sam", "convo_id": "c-s", "message": "hi sam"})
    finally:
        prov._env_key = real
    aria = (
        await client.get("/v1/conversations", params={"persona_id": "aria"}, headers=_headers("u1"))
    ).json()
    assert {c["convo_id"] for c in aria} == {"c-a"}
    assert all(c["persona_id"] == "aria" for c in aria)


async def test_cross_user_isolation(client) -> None:
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731
    try:
        await _stream_mock(
            client,
            {"persona_id": "aria", "convo_id": "c-u1", "message": "mine"},
            user="u1",
        )
        await _stream_mock(
            client,
            {"persona_id": "aria", "convo_id": "c-u2", "message": "theirs"},
            user="u2",
        )
    finally:
        prov._env_key = real
    u1 = (await client.get("/v1/conversations", headers=_headers("u1"))).json()
    u2 = (await client.get("/v1/conversations", headers=_headers("u2"))).json()
    assert {c["convo_id"] for c in u1} == {"c-u1"}
    assert {c["convo_id"] for c in u2} == {"c-u2"}


async def test_before_cursor_excludes_recent(client) -> None:
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731
    try:
        await _stream_mock(client, {"persona_id": "aria", "convo_id": "c-old", "message": "first"})
        convos = (await client.get("/v1/conversations", headers=_headers("u1"))).json()
        boundary = convos[0]["last_activity"]
        await _stream_mock(client, {"persona_id": "aria", "convo_id": "c-new", "message": "second"})
    finally:
        prov._env_key = real
    # Cursor at the old convo's last_activity excludes everything at-or-after it.
    older = (
        await client.get("/v1/conversations", params={"before": boundary}, headers=_headers("u1"))
    ).json()
    assert all(c["convo_id"] != "c-new" for c in older)


async def test_list_events_convo_id_filter(client) -> None:
    """K6: ``GET /v1/memory?convo_id=`` returns only that thread's events so
    the UI can lazy-load one conversation's history without pulling the whole
    persona timeline."""
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731
    try:
        await _stream_mock(client, {"persona_id": "aria", "convo_id": "c-a", "message": "in A"})
        await _stream_mock(client, {"persona_id": "aria", "convo_id": "c-b", "message": "in B"})
    finally:
        prov._env_key = real
    a = (
        await client.get(
            "/v1/memory", params={"persona_id": "aria", "convo_id": "c-a"}, headers=_headers("u1")
        )
    ).json()
    b = (
        await client.get(
            "/v1/memory", params={"persona_id": "aria", "convo_id": "c-b"}, headers=_headers("u1")
        )
    ).json()
    # Each thread is exactly its own user+assistant pair; no cross-convo leak.
    a_contents = {e["content"] for e in a}
    b_contents = {e["content"] for e in b}
    assert "in A" in a_contents and "in B" not in a_contents
    assert "in B" in b_contents and "in A" not in b_contents
    assert len(a) == 2 and len(b) == 2
