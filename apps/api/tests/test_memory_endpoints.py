"""``/v1/memory`` endpoints + persistence via ``/v1/llm/stream``.

A mock stream turn must persist the user + assistant events; ``GET /v1/memory``
then returns the timeline and ``POST /v1/memory/recall`` returns chains.
"""

from __future__ import annotations

import json

import ai_companion_api.llm.provider as prov


async def _stream_mock(client, body: dict) -> None:
    """POST a stream and drain it so the persist-after-done side effect runs."""
    async with client.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass


async def test_llm_stream_persists_events(client) -> None:
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731  force mock adapter
    try:
        await _stream_mock(
            client, {"persona_id": "aria", "convo_id": "c1", "message": "My dog Maple died."}
        )
    finally:
        prov._env_key = real

    r = await client.get("/v1/memory", params={"persona_id": "aria"})
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 2  # user + assistant
    assert events[0]["role"] == "user"
    assert events[0]["content"] == "My dog Maple died."
    assert events[1]["role"] == "assistant"
    # prev_event link is persisted on the wire as the contracts Event field.
    assert events[1]["prev_event_id"] == events[0]["id"]
    # No key material leaks into persisted content.
    assert "sk-" not in json.dumps(events)


async def test_recall_endpoint_returns_chains_after_stream(client) -> None:
    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731
    try:
        await _stream_mock(
            client, {"persona_id": "aria", "convo_id": "c1", "message": "My dog Maple died."}
        )
    finally:
        prov._env_key = real

    r = await client.post(
        "/v1/memory/recall",
        json={"persona_id": "aria", "query": "What was the name of my dog?"},
    )
    assert r.status_code == 200
    chains = r.json()
    assert chains, "expected at least one recalled chain"
    blob = " ".join(e["content"] for ch in chains for e in ch["events"])
    assert "Maple" in blob
    # Each chain reports a salience sum.
    for ch in chains:
        assert "salience_sum" in ch


async def test_memory_empty_when_no_history(client) -> None:
    r = await client.get("/v1/memory", params={"persona_id": "aria"})
    assert r.status_code == 200
    assert r.json() == []


async def test_recall_empty_when_no_history(client) -> None:
    r = await client.post(
        "/v1/memory/recall",
        json={"persona_id": "aria", "query": "anything"},
    )
    assert r.status_code == 200
    assert r.json() == []
