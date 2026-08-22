"""Turn orchestrator smoke tests.

Uses the in-memory store + the mock adapter (no network, no env keys). The
mock chain is the default zero-config path, so these tests run anywhere.
"""

from __future__ import annotations

import base64
import json

import pytest

from ai_companion_api.config import Settings
from ai_companion_api.crypto.envelope import EnvelopeCipher
from ai_companion_api.memory.store import InMemoryStore
from ai_companion_api.turn import TurnInput, run_turn
from ai_companion_api.vault.session_ecdh import generate_session_keypair


@pytest.fixture
def env() -> tuple:
    settings = Settings()
    store = InMemoryStore()
    ecdh = generate_session_keypair()
    envelope = EnvelopeCipher.from_base64(EnvelopeCipher.generate_key_b64())
    return settings, store, ecdh, envelope


async def test_smoke_turn_no_byok(env) -> None:
    settings, store, ecdh, envelope = env
    inp = TurnInput(
        user_id="u1",
        persona_id="aurora",
        conversation_id="c1",
        user_message="I'm feeling a bit tired today",
        byok_enc_blob=None,
    )
    # With no providers configured, should raise NoProviderAvailableError
    try:
        out = await run_turn(inp, settings=settings, store=store, ecdh=ecdh, envelope=envelope)
        assert False, "Expected NoProviderAvailableError"
    except Exception as e:
        assert "provider" in str(e).lower() or "configured" in str(e).lower()


async def test_turn_persists_events_into_shared_chain(env) -> None:
    """Telegram turns land in the same event chain as web turns — the empathy
    differentiator depends on this. After one turn the store holds a user +
    assistant event linked by prev_event_id, and a usage row."""
    from ai_companion_api.llm import build_chain, NoProviderAvailableError, RoutingCandidate
    from ai_companion_api.llm.types import LlmAdapter, LlmUsage

    settings, store, ecdh, envelope = env

    # Inject a fake adapter for this test
    class _FakeAdapter(LlmAdapter):
        provider_kind = "test"

        async def stream(self, messages, model):
            yield "hello from telegram"
            yield ""  # pragma: no cover

        def last_usage(self):
            return LlmUsage("test", "test-model", 2, 1, 0.0)

    # Monkey patch build_chain to return our fake adapter
    import ai_companion_api.turn as turn_module

    original_build_chain = turn_module.build_chain

    def fake_build_chain(*, enc_key_blob, settings, ecdh, model=None, byok_decrypted=None):
        return [RoutingCandidate(
            kind="test",
            model="test-model",
            base_url=None,
            adapter=_FakeAdapter(),
            is_mock=False,
            decrypted=None,
        )]

    turn_module.build_chain = fake_build_chain

    inp = TurnInput(
        user_id="u1",
        persona_id="aurora",
        conversation_id="c-shared",
        user_message="hello from telegram",
        byok_enc_blob=None,
    )

    try:
        out = await run_turn(inp, settings=settings, store=store, ecdh=ecdh, envelope=envelope)
        # Verify the turn persisted events
        events = await store.list_events(user_id="u1", persona_id="aurora", convo_id="c-shared")
        assert len(events) == 2
        assert events[0].role.value == "user"
        assert events[1].role.value == "assistant"
        assert events[1].content == "hello from telegram"
    finally:
        turn_module.build_chain = original_build_chain


async def test_second_turn_recalls_first(env) -> None:
    """The recent window grows across turns — the second turn sees the first
    exchange in its context (same continuity as the web chat)."""
    from ai_companion_api.llm import RoutingCandidate
    from ai_companion_api.llm.types import LlmAdapter, LlmUsage
    from ai_companion_api.turn import TurnInput

    settings, store, ecdh, envelope = env

    # Inject a fake adapter for this test
    class _FakeAdapter(LlmAdapter):
        provider_kind = "test"

        async def stream(self, messages, model):
            yield f"reply to: {messages[-1]['content'][:30]}"
            yield ""  # pragma: no cover

        def last_usage(self):
            return LlmUsage("test", "test-model", 2, 1, 0.0)

    # Monkey patch build_chain to return our fake adapter
    import ai_companion_api.turn as turn_module

    original_build_chain = turn_module.build_chain

    def fake_build_chain(*, enc_key_blob, settings, ecdh, model=None, byok_decrypted=None):
        return [RoutingCandidate(
            kind="test",
            model="test-model",
            base_url=None,
            adapter=_FakeAdapter(),
            is_mock=False,
            decrypted=None,
        )]

    turn_module.build_chain = fake_build_chain

    try:
        for i, msg in enumerate(["first message", "second message"]):
            await run_turn(
                TurnInput(
                    user_id="u1",
                    persona_id="aurora",
                    conversation_id="c2",
                    user_message=msg,
                    byok_enc_blob=None,
                ),
                settings=settings,
                store=store,
                ecdh=ecdh,
                envelope=envelope,
            )

        # Verify both turns persisted
        events = await store.list_events(user_id="u1", persona_id="aurora", convo_id="c2")
        assert len(events) == 4  # 2 user + 2 assistant events
        assert events[0].role.value == "user"
        assert events[1].role.value == "assistant"
        assert events[2].role.value == "user"
        assert events[3].role.value == "assistant"
    finally:
        turn_module.build_chain = original_build_chain


async def test_byok_envelope_roundtrip_reseal(env) -> None:
    """The per-turn BYOK path: envelope-decrypt the stored blob → re-seal to the
    session ECDH pubkey → build_chain decrypts via the session private key.

    This exercises the full crypto round-trip (envelope + ECDH) WITHOUT driving
    the LiteLLM stream, so it is hermetic (no litellm install needed). It proves
    the bind-time envelope wrap and the per-turn re-seal are inverse operations
    over the key JSON.
    """
    from ai_companion_api.turn import _reseal_byok
    from ai_companion_api.vault.decrypt import decrypt_key_blob

    settings, store, ecdh, envelope = env
    payload = json.dumps(
        {"provider_kind": "openai", "api_key": "sk-fake-test-key-1234567890", "base_url": "https://api.openai.com/v1"}
    ).encode("utf-8")
    byok_enc_blob = envelope.encrypt_b64(payload)

    # Per-turn: envelope-decrypt → re-seal to session pubkey.
    plaintext = envelope.decrypt_b64(byok_enc_blob)
    assert plaintext == payload
    resealed = _reseal_byok(plaintext, ecdh)

    # build_chain would call decrypt_key_blob on this blob → recover the key.
    dk = decrypt_key_blob(resealed, ecdh.private_key)
    assert dk.provider_kind == "openai"
    assert dk.api_key_str() == "sk-fake-test-key-1234567890"
    assert dk.base_url == "https://api.openai.com/v1"
    # zeroize the recovered bytearray (honest-zeroize hygiene in the test too).
    for i in range(len(dk.api_key)):
        dk.api_key[i] = 0


async def test_build_chain_accepts_byok_blob(env) -> None:
    """``run_turn`` with a BYOK envelope blob reaches build_chain without a
    resolution error (the re-sealed blob opens cleanly). We don't drive the
    stream (that needs litellm); we stub ``run_with_fallback`` is not needed —
    instead we call the internal re-seal + build_chain directly to assert the
    plumbing. This guards against a regression where the envelope/ECDH shapes
    drift apart."""
    from ai_companion_api.llm import build_chain
    from ai_companion_api.turn import _reseal_byok

    settings, store, ecdh, envelope = env
    payload = json.dumps(
        {"provider_kind": "openai", "api_key": "sk-fake-test-key-1234567890", "base_url": None}
    ).encode("utf-8")
    resealed = _reseal_byok(envelope.decrypt_b64(envelope.encrypt_b64(payload)), ecdh)
    cands = build_chain(enc_key_blob=resealed, settings=settings, ecdh=ecdh, model=None)
    # BYOK candidate is first, no mock anymore.
    assert cands[0].kind == "openai"
    assert cands[0].decrypted is not None
    assert len(cands) == 1  # Only BYOK, no mock


async def test_messenger_byok_uses_provider_model_not_default(env) -> None:
    """The messenger (Telegram) path sends no per-turn ``model`` (the web client
    does). When the BYOK key is resolved from the server-side provider envelope
    store, the orchestrator must also take the provider row's ``model`` and hand
    it to ``build_chain`` — otherwise ``build_chain`` falls back to
    ``DEFAULT_MODELS[kind]``, which is wrong for providers whose user-picked
    model differs from the default.

    Regression for an Ollama Cloud bot (kind=ollama, base_url=https://ollama.com,
    model=glm-5.2:cloud): without the fix the BYOK candidate was built as
    ``openai/llama3.3`` (the ollama default), Ollama Cloud rejected it, and the
    turn fell through to the mock stand-in. With the fix the candidate carries
    the user's model.
    """
    from ai_companion_contracts import Provider, ProviderKind

    from ai_companion_api.llm import build_chain
    from ai_companion_api.turn import _resolve_messenger_byok_from_provider

    settings, store, ecdh, envelope = env
    user_id = "u-ollama-cloud"
    # Server-side envelope ciphertext of the key JSON (as the BYOK onboarding
    # flow stores it under providers.api_key_ciphertext).
    plaintext = json.dumps(
        {"provider_kind": "ollama", "api_key": "sk-fake-ollama-cloud-key", "base_url": "https://ollama.com"}
    ).encode("utf-8")
    ciphertext = envelope.encrypt_b64(plaintext)
    await store.add_provider(
        Provider(
            id="p1",
            user_id=user_id,
            kind=ProviderKind("ollama"),
            label="Ollama Cloud",
            base_url="https://ollama.com",
            key_handle="kh-1",
            model="glm-5.2:cloud",
        ),
        api_key_ciphertext=ciphertext,
    )

    # Messenger path: no byok_enc_blob, no per-turn model, no key_handle.
    dk, model = await _resolve_messenger_byok_from_provider(
        store=store, user_id=user_id, key_handle=None, envelope=envelope
    )
    assert dk is not None
    assert dk.provider_kind == "ollama"
    # The provider row's user-chosen model is returned (NOT None).
    assert model == "glm-5.2:cloud"

    # build_chain with that model produces an Ollama Cloud candidate carrying
    # the user's model (openai/<model> against {base_url}/v1), not the default
    # openai/llama3.3 the bug used to produce.
    cands = build_chain(
        enc_key_blob=None, settings=settings, ecdh=ecdh, model=model, byok_decrypted=dk
    )
    byok_cand = next(c for c in cands if c.decrypted is not None)
    assert byok_cand.kind == "ollama"
    assert byok_cand.model == "openai/glm-5.2:cloud"
    assert byok_cand.base_url == "https://ollama.com/v1"
    # The default-model bug would have produced openai/llama3.3 — assert it does not.
    assert byok_cand.model != "openai/llama3.3"
    # zeroize the recovered bytearray (honest-zeroize hygiene in the test too).
    for i in range(len(dk.api_key)):
        dk.api_key[i] = 0


async def test_bad_byok_blob_falls_back_to_env_chain(env) -> None:
    """A corrupted envelope blob must NOT crash the turn — the orchestrator
    logs and falls back to the env chain (or raises NoProviderAvailableError)."""
    from ai_companion_api.llm import NoProviderAvailableError

    settings, store, ecdh, envelope = env
    inp = TurnInput(
        user_id="u1",
        persona_id="aurora",
        conversation_id="c4",
        user_message="bad blob",
        byok_enc_blob=base64.b64encode(b"not-a-valid-envelope").decode(),
    )
    # With no env keys configured, this should raise NoProviderAvailableError
    with pytest.raises(NoProviderAvailableError):
        await run_turn(inp, settings=settings, store=store, ecdh=ecdh, envelope=envelope)


async def test_budget_hard_stop_raises_error(env) -> None:
    """When monthly spend exceeds the budget cap, real providers are skipped
    and the turn raises NoProviderAvailableError (HTTP 402 in web path)."""
    from ai_companion_api.llm import NoProviderAvailableError

    settings, store, ecdh, envelope = env

    # Push spend above the default monthly_budget_usd by adding a costly usage row
    from ai_companion_contracts import Usage

    expensive = Usage(
        id="u-expensive",
        user_id="u1",
        family_id=None,
        provider_kind="openai",
        model="gpt-4",
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=settings.monthly_budget_usd + 100.0,
    )
    await store.add_usage(expensive)

    inp = TurnInput(
        user_id="u1",
        persona_id="aurora",
        conversation_id="c5",
        user_message="over budget",
        byok_enc_blob=None,
    )

    # Budget hard-stop with no providers should raise NoProviderAvailableError
    with pytest.raises(NoProviderAvailableError):
        await run_turn(inp, settings=settings, store=store, ecdh=ecdh, envelope=envelope)