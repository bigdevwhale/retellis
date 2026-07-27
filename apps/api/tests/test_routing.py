"""Phase 4 — routing chain, budget thresholds, fallback runner, /v1/routing.

Covers: ``compute_budget`` thresholds; ``build_chain`` ordering (BYOK → env →
Ollama → mock, BYOK skips its own env kind); ``run_with_fallback`` falling over
a failing provider to mock; budget hard-stop skipping real providers via the
stream; ``GET /v1/routing`` returning the chain + per-provider summary + budget.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator

import pytest
from ai_companion_contracts import Usage
from nacl.public import SealedBox

from ai_companion_api.config import Settings
from ai_companion_api.llm import LlmCallError, MockAdapter, RoutingCandidate, build_chain
from ai_companion_api.llm.types import LlmAdapter, LlmUsage
from ai_companion_api.memory.store import UsageRecord
from ai_companion_api.routing import (
    clear_fallback,
    compute_budget,
    last_fallback,
    routing_state,
    run_with_fallback,
)
from ai_companion_api.routing.router import display_chain
from ai_companion_api.vault.session_ecdh import generate_session_keypair

# --- budget -----------------------------------------------------------------


def test_budget_thresholds() -> None:
    low = compute_budget(spent_usd=5.0, monthly_budget_usd=20.0)
    assert not low.warn and not low.hard_stop
    assert low.remaining_usd == 15.0
    assert abs(low.pct - 0.25) < 1e-9

    warn = compute_budget(spent_usd=16.0, monthly_budget_usd=20.0)
    assert warn.warn and not warn.hard_stop

    stop = compute_budget(spent_usd=20.0, monthly_budget_usd=20.0)
    assert stop.warn and stop.hard_stop
    assert stop.remaining_usd == 0.0

    over = compute_budget(spent_usd=25.0, monthly_budget_usd=20.0)
    assert over.hard_stop and over.remaining_usd == 0.0

    nocap = compute_budget(spent_usd=999.0, monthly_budget_usd=0.0)
    assert not nocap.warn and not nocap.hard_stop


# --- build_chain ------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base: dict = {
        "litellm_api_key_openai": "",
        "litellm_api_key_openrouter": "",
        "ollama_base_url": "",
        "monthly_budget_usd": 20.0,
    }
    base.update(overrides)
    return Settings(**base)


def test_build_chain_no_keys_is_just_mock() -> None:
    ecdh = generate_session_keypair()
    cands = build_chain(enc_key_blob=None, settings=_settings(), ecdh=ecdh)
    assert len(cands) == 1
    assert cands[0].kind == "mock" and cands[0].is_mock


def test_build_chain_env_then_mock() -> None:
    ecdh = generate_session_keypair()
    cands = build_chain(
        enc_key_blob=None,
        settings=_settings(litellm_api_key_openai="k-openai"),
        ecdh=ecdh,
    )
    kinds = [c.kind for c in cands]
    assert kinds == ["openai", "mock"]
    assert all(c.decrypted is None for c in cands)


def test_build_chain_ollama_inserted_before_mock_when_configured() -> None:
    ecdh = generate_session_keypair()
    cands = build_chain(
        enc_key_blob=None,
        settings=_settings(
            litellm_api_key_openai="k-openai", ollama_base_url="http://ollama:11434"
        ),
        ecdh=ecdh,
    )
    kinds = [c.kind for c in cands]
    assert kinds == ["openai", "ollama", "mock"]
    assert cands[1].base_url == "http://ollama:11434"


def _seal_blob(payload: dict, ecdh) -> str:
    return base64.b64encode(
        SealedBox(ecdh.private_key.public_key).encrypt(json.dumps(payload).encode())
    ).decode()


def test_build_chain_byok_first_and_skips_same_env_kind() -> None:
    ecdh = generate_session_keypair()
    blob = _seal_blob({"provider_kind": "openai", "api_key": "sk-byok"}, ecdh)
    cands = build_chain(
        enc_key_blob=blob,
        settings=_settings(litellm_api_key_openai="k-env-openai"),
        ecdh=ecdh,
    )
    # BYOK openai first; the env openai entry is dropped (same kind); mock last.
    kinds = [c.kind for c in cands]
    assert kinds[0] == "openai"
    assert kinds.count("openai") == 1
    assert kinds[-1] == "mock"
    # The BYOK candidate carries the decrypted key; env/mock do not.
    assert cands[0].decrypted is not None
    assert cands[0].decrypted.api_key.decode() == "sk-byok"
    assert all(c.decrypted is None for c in cands[1:])


def test_build_chain_byok_ollama_cloud_uses_openai_compat_endpoint() -> None:
    """Ollama Cloud (non-local base_url + api_key) routes through the
    OpenAI-compatible endpoint, not LiteLLM's native ollama provider."""
    ecdh = generate_session_keypair()
    blob = _seal_blob(
        {
            "provider_kind": "ollama",
            "api_key": "ollama-cloud-key",
            "base_url": "https://cloud.ollama.com",
        },
        ecdh,
    )
    cands = build_chain(enc_key_blob=blob, settings=_settings(), ecdh=ecdh, model="glm-5.2:cloud")
    byok = cands[0]
    assert byok.kind == "ollama"  # provider_kind metadata stays ollama
    assert byok.model == "openai/glm-5.2:cloud"  # routed via openai/ prefix
    assert byok.base_url == "https://cloud.ollama.com/v1"
    assert byok.decrypted is not None
    assert byok.decrypted.api_key.decode() == "ollama-cloud-key"


def test_build_chain_byok_ollama_cloud_appends_v1_only_once() -> None:
    ecdh = generate_session_keypair()
    blob = _seal_blob(
        {"provider_kind": "ollama", "api_key": "k", "base_url": "https://ollama.com/v1/"},
        ecdh,
    )
    cands = build_chain(enc_key_blob=blob, settings=_settings(), ecdh=ecdh, model="qwen3-coder")
    assert cands[0].model == "openai/qwen3-coder"
    assert cands[0].base_url == "https://ollama.com/v1"


def test_build_chain_byok_ollama_local_keeps_native_prefix() -> None:
    """A local Ollama endpoint (no key needed) keeps the native ollama/ prefix."""
    ecdh = generate_session_keypair()
    blob = _seal_blob(
        {"provider_kind": "ollama", "api_key": "k", "base_url": "http://localhost:11434"},
        ecdh,
    )
    cands = build_chain(enc_key_blob=blob, settings=_settings(), ecdh=ecdh, model="llama3.3")
    assert cands[0].model == "ollama/llama3.3"
    assert cands[0].base_url == "http://localhost:11434"


# --- run_with_fallback ------------------------------------------------------


class _FailingAdapter(LlmAdapter):
    provider_kind = "openai"

    async def stream(self, messages, model) -> AsyncIterator[str]:  # noqa: ANN001
        raise LlmCallError("provider call failed: ConnectionError")
        yield ""  # pragma: no cover

    def last_usage(self) -> LlmUsage:
        return LlmUsage("openai", "gpt-4o-mini", 0, 0, 0.0)


@pytest.mark.asyncio
async def test_run_with_fallback_falls_over_to_mock() -> None:
    clear_fallback("u")
    cands = [
        RoutingCandidate("openai", "gpt-4o-mini", None, _FailingAdapter(), False, None),
        RoutingCandidate("mock", "mock", None, MockAdapter(), True, None),
    ]
    tags: list[tuple[str, object]] = []
    async for tag, val in run_with_fallback(
        cands, [{"role": "user", "content": "hi"}], user_id="u"
    ):
        tags.append((tag, val))
    kinds = [t[0] for t in tags]
    assert "fallback" in kinds
    fb = next(v for t, v in tags if t == "fallback")
    assert fb[0] == "openai" and fb[1] == "mock"
    assert kinds[-1] == "served"
    assert last_fallback("u") and "openai" in last_fallback("u")


# --- routing_state rollup ---------------------------------------------------


def _rec(user_id: str, kind: str, cost: float, pt: int, ct: int) -> UsageRecord:
    from datetime import UTC, datetime

    return UsageRecord(
        usage=Usage(
            id=f"{kind}-{cost}-{pt}",
            user_id=user_id,
            provider_kind=kind,
            model="m",
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=cost,
        ),
        created_at=datetime.now(UTC),
    )


def test_routing_state_summary_and_budget() -> None:
    settings = _settings(litellm_api_key_openai="k", monthly_budget_usd=10.0)
    records = [
        _rec("u", "openai", 4.0, 100, 50),
        _rec("u", "openai", 2.0, 20, 10),
        _rec("u", "mock", 0.0, 5, 8),
    ]
    state = routing_state(
        settings=settings, records=records, fallback_last_turn="openai → mock (x)"
    )
    # Chain: configured openai + ollama(standby) + mock.
    chain_kinds = [n.kind for n in state.chain]
    assert chain_kinds == ["openai", "ollama", "mock"]
    assert state.chain[-1].status == "healthy"
    # Budget: spent 6 / 10 → 60% → no warn, no hard-stop.
    assert abs(state.spent_usd - 6.0) < 1e-9
    assert abs(state.remaining_usd - 4.0) < 1e-9
    assert not state.warn and not state.hard_stop
    assert state.fallback_last_turn == "openai → mock (x)"
    # Per-provider rollup: openai aggregated, mock separate.
    by_kind = {p.kind: p for p in state.per_provider}
    assert by_kind["openai"].requests == 2
    assert abs(by_kind["openai"].cost_usd - 6.0) < 1e-9
    assert by_kind["openai"].tokens_in == 120
    assert by_kind["openai"].tokens_out == 60
    assert by_kind["mock"].requests == 1
    # Langfuse link-out is the browser URL.
    assert state.langfuse_url == settings.langfuse_public_url


def test_display_chain_zero_config_is_ollama_standby_then_mock() -> None:
    chain = display_chain(_settings())
    assert [n.kind for n in chain] == ["ollama", "mock"]
    assert chain[0].status == "standby"
    assert chain[1].status == "healthy"


# --- /v1/routing endpoint + budget hard-stop via the stream -----------------


async def _read_events(client, body: dict) -> list[dict]:
    events: list[dict] = []
    async with client.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


async def test_get_routing_returns_state_with_summary(client) -> None:
    import ai_companion_api.llm.provider as prov

    real = prov._env_key
    prov._env_key = lambda settings, kind: None  # noqa: E731
    try:
        events = await _read_events(
            client, {"persona_id": "aria", "convo_id": "c1", "message": "hello there"}
        )
    finally:
        prov._env_key = real
    assert any(e["type"] == "usage" for e in events)

    r = await client.get("/v1/routing")
    assert r.status_code == 200
    state = r.json()
    chain_kinds = [n["kind"] for n in state["chain"]]
    assert chain_kinds[-1] == "mock"
    assert "pct" in state and "per_provider" in state
    by_kind = {p["kind"]: p for p in state["per_provider"]}
    assert by_kind["mock"]["requests"] >= 1
    assert "sk-" not in json.dumps(state)


async def test_budget_hard_stop_skips_to_mock(client) -> None:
    import ai_companion_api.llm.provider as prov

    app = client._transport.app  # type: ignore[attr-defined]
    # Pre-charge spend past a tiny cap so the budget hard-stop triggers.
    await app.state.store.add_usage(
        Usage(
            id="precharge",
            user_id=app.state.settings.default_user_id,
            provider_kind="openai",
            model="gpt-4o-mini",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.05,
        )
    )
    app.state.settings.monthly_budget_usd = 0.01

    real = prov._env_key
    # Make ONLY env openai "configured" so there's a real provider to skip via
    # the budget hard-stop. Returning a key for every kind would also light up
    # the bedrock env candidate, which raises ProviderResolutionError (it needs
    # the full AWS triplet) — that short-circuits the stream with an `error`
    # event before the budget gate ever runs.
    prov._env_key = lambda settings, kind: "k" if kind == "openai" else None  # noqa: E731
    try:
        events = await _read_events(
            client, {"persona_id": "sam", "convo_id": "c9", "message": "rough day"}
        )
    finally:
        prov._env_key = real
        app.state.settings.monthly_budget_usd = 20.0

    types = [e["type"] for e in events]
    # A fallback event with reason mentioning the budget hard-stop must fire,
    # and the turn is still served by mock (tokens + usage arrive).
    assert "fallback" in types
    fb = next(e for e in events if e["type"] == "fallback")
    assert "budget hard-stop" in fb["reason"]
    assert fb["to_kind"] == "mock"
    assert any(e["type"] == "token" for e in events)
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["provider_kind"] == "mock"
    assert types[-1] == "done"
    # No key material leaks.
    assert "sk-" not in json.dumps(events)
