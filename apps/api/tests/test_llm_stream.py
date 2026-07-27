"""``POST /v1/llm/stream`` — SSE shape, mock adapter, fallback event, bad blob."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from ai_companion_api.llm import LlmCallError, MockAdapter, RoutingCandidate
from ai_companion_api.llm.types import LlmAdapter, LlmUsage
from ai_companion_api.routers import llm as llm_router


async def _read_events(client, body: dict) -> list[dict]:
    """POST a stream request and collect the JSON event payloads in order."""
    events: list[dict] = []
    async with client.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


async def test_mock_stream_shape(client) -> None:
    # Force the env-fallback path off so we exercise the mock adapter regardless
    # of any LITELLM_API_KEY_* present in the dev shell.
    import ai_companion_api.llm.provider as prov

    real_env_key = prov._env_key

    def fake_env_key(settings, kind):  # noqa: ANN001
        return None

    prov._env_key = fake_env_key
    try:
        events = await _read_events(
            client,
            {"persona_id": "aria", "convo_id": "c1", "message": "I feel stuck today."},
        )
    finally:
        prov._env_key = real_env_key

    types = _types(events)
    assert types[0] == "session"
    assert types[-1] == "done"
    # session → token×N → usage → done (no fallback on the happy mock path)
    assert "fallback" not in types
    usage_idx = types.index("usage")
    assert usage_idx == len(types) - 2
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens).strip()  # non-empty reply
    usage = events[usage_idx]
    assert usage["provider_kind"] == "mock"
    assert usage["completion_tokens"] > 0


class _FailingAdapter(LlmAdapter):
    """Real-provider stand-in that always blows up so fallback triggers."""

    provider_kind = "openai"

    async def stream(self, messages, model) -> AsyncIterator[str]:  # noqa: ANN001
        raise LlmCallError("provider call failed: ConnectionError")
        yield ""  # pragma: no cover  # make it an async generator

    def last_usage(self) -> LlmUsage:
        return LlmUsage("openai", "gpt-4o-mini", 0, 0, 0.0)


async def test_fallback_to_mock_on_provider_failure(client, monkeypatch) -> None:
    def fake_build_chain(*, enc_key_blob, settings, ecdh, model=None, byok_decrypted=None):  # noqa: ANN001, ARG001
        return [
            RoutingCandidate(
                kind="openai",
                model="gpt-4o-mini",
                base_url=None,
                adapter=_FailingAdapter(),
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
    events = await _read_events(
        client,
        {"persona_id": "sam", "convo_id": "c2", "message": "rough day"},
    )
    types = _types(events)
    assert "fallback" in types
    fb = next(e for e in events if e["type"] == "fallback")
    assert fb["from_kind"] == "openai"
    assert fb["to_kind"] == "mock"
    # Mock tokens still arrive after the fallback.
    assert [e for e in events if e["type"] == "token"]
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["provider_kind"] == "mock"
    assert types[-1] == "done"


async def test_bad_blob_emits_redacted_error(client, monkeypatch) -> None:
    events = await _read_events(
        client,
        {
            "persona_id": "aria",
            "convo_id": "c1",
            "message": "hi",
            "enc_key_blob": "not-valid-base64!!!",
        },
    )
    types = _types(events)
    assert types[0] == "session"
    assert "error" in types
    err = next(e for e in events if e["type"] == "error")
    assert "sk-" not in err["message"]
    assert types[-1] == "done"


# --- family-scope validations (mutual exclusion, cross-family, joint rules)
#
# These run in the HTTP handler (pre-flight) so 4xx errors surface as
# ``response.status_code`` on the SSE response, NOT as a mid-stream error
# event — the SSE stream never opens. ``_read_events`` would hang waiting
# for `data:` lines that never come, so we use a direct ``client.post``
# here instead.


async def test_personal_and_family_blobs_mutually_exclusive(client) -> None:
    """The plan: personal ``enc_key_blob`` and family ``family_enc_key_blob``
    cannot both be set on the same turn. Both set → 400 so the user gets a
    clear error and we never decrypt both into memory."""
    resp = await client.post(
        "/v1/llm/stream",
        json={
            "persona_id": "fam",
            "convo_id": "c1",
            "message": "hi",
            "enc_key_blob": "aGVsbG8=",
            "family_enc_key_blob": "d29ybGQ=",
            "family_id": "fam-1",
            "family_key_handle": "fam-kh-1",
            "visibility": "private",
            "participant_user_id": "u1",
        },
    )
    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert "mutually exclusive" in body
    # No key material ever appears in the error detail.
    assert "sk-" not in body
    assert "aGVsbG8=" not in body
    assert "d29ybGQ=" not in body


async def test_family_blob_without_family_id_rejected(client) -> None:
    """A family blob with no family scope would be silently ignored by the
    chain builder (env-chain + personal budget instead of the family key) —
    a client bug that must fail loudly with 400, not degrade."""
    resp = await client.post(
        "/v1/llm/stream",
        json={
            "persona_id": "fam",
            "convo_id": "c1",
            "message": "hi",
            "family_enc_key_blob": "d29ybGQ=",
        },
    )
    assert resp.status_code == 400
    assert "family_id" in resp.json()["detail"]


async def test_visibility_shared_without_family_id_rejected(client) -> None:
    """``visibility=shared`` MUST have ``family_id`` set. Shared without a
    family is meaningless and would silently drop scope. Reject 400."""
    resp = await client.post(
        "/v1/llm/stream",
        json={
            "persona_id": "fam",
            "convo_id": "c1",
            "message": "hi",
            "visibility": "shared",
        },
    )
    assert resp.status_code == 400
    assert "family_id" in resp.json()["detail"]


async def test_participant_user_id_must_match_principal(client) -> None:
    """``participant_user_id`` defaults to principal.user_id; if supplied,
    it must equal principal.user_id. A different value would let one member
    impersonate another in the recall scope. Reject 403."""
    # The default insecure-header escape-hatch principal has user_id
    # = settings.default_user_id; we use a different value to trigger 403.
    resp = await client.post(
        "/v1/llm/stream",
        json={
            "persona_id": "fam",
            "convo_id": "c1",
            "message": "hi",
            "family_id": "fam-1",
            "visibility": "private",
            "participant_user_id": "definitely-not-me",
        },
    )
    assert resp.status_code == 403
    assert "participant_user_id" in resp.json()["detail"]
