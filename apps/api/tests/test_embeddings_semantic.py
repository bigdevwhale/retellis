"""Semantic embedder: batching, LRU cache, failure fallback, store wiring.

The litellm call is stubbed at ``SemanticEmbedder._call`` so no network/API key
is involved. The invariants under test: one batch call covers query+candidates,
the cache prevents re-embedding unchanged history, any failure yields ``None``
(hash fallback — recall never breaks), and ``make_semantic_embedder`` gates on
mode+key.
"""

from __future__ import annotations

import uuid

import pytest
from ai_companion_contracts import EventRole

from ai_companion_api.config import Settings
from ai_companion_api.memory import InMemoryStore, append_event
from ai_companion_api.memory.embeddings import EMBED_DIM, cosine, embed
from ai_companion_api.memory.embeddings_semantic import (
    SemanticEmbedder,
    make_semantic_embedder,
    rank_chains_semantic,
)

USER = "u-emb"
PERSONA = "companion"
CONVO = "c-emb"


def _fake_vec(seed: int) -> list[float]:
    # Distinct deterministic unnormalized vectors; the embedder normalizes.
    return [float(seed)] + [1.0] * (EMBED_DIM - 1)


class _CountingEmbedder(SemanticEmbedder):
    def __init__(self, *, fail: bool = False, model: str | None = None) -> None:
        # The vector cache is process-wide and keyed by (model, text) — give
        # each test instance a unique model so tests never share cache entries.
        super().__init__(model=model or f"test-{uuid.uuid4().hex}", api_key="k")
        self.calls: list[list[str]] = []
        self._fail = fail

    async def _call(self, texts: list[str]) -> list[list[float]] | None:
        self.calls.append(list(texts))
        if self._fail:
            return None
        return [_fake_vec(len(t)) for t in texts]


@pytest.mark.asyncio
async def test_embed_batch_normalizes_and_returns_in_order() -> None:
    e = _CountingEmbedder()
    out = await e.embed_batch(["alpha", "longer text here"])
    assert out is not None
    assert len(out) == 2
    for vec in out:
        assert len(vec) == EMBED_DIM
        assert sum(v * v for v in vec) == pytest.approx(1.0)
    # cosine of a vector with itself is 1 after normalization
    assert cosine(out[0], out[0]) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_embed_batch_caches_repeat_texts() -> None:
    e = _CountingEmbedder()
    await e.embed_batch(["hello", "world"])
    assert e.calls == [["hello", "world"]]
    out = await e.embed_batch(["hello", "world", "new"])
    assert out is not None
    # Only the uncached text hits the API on the second call.
    assert e.calls[1] == ["new"]


@pytest.mark.asyncio
async def test_embed_batch_returns_none_on_failure() -> None:
    e = _CountingEmbedder(fail=True)
    assert await e.embed_batch(["anything"]) is None


@pytest.mark.asyncio
async def test_rank_chains_semantic_returns_none_on_failure() -> None:
    store = InMemoryStore()
    ev = await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    e = _CountingEmbedder(fail=True)
    assert await rank_chains_semantic(e, [ev], "what was my dog's name?") is None


@pytest.mark.asyncio
async def test_recall_chains_falls_back_to_hash_on_embed_failure() -> None:
    store = InMemoryStore(semantic_embedder=_CountingEmbedder(fail=True))
    await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    chains = await store.recall_chains(
        user_id=USER, persona_id=PERSONA, query="What was the name of my dog?"
    )
    # Hash fallback still recalls — the turn is never broken by embed failure.
    assert chains
    assert any("Maple" in ev.content for ch in chains for ev in ch.events)


@pytest.mark.asyncio
async def test_recall_chains_uses_semantic_when_available() -> None:
    emb = _CountingEmbedder()
    store = InMemoryStore(semantic_embedder=emb)
    await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    chains = await store.recall_chains(
        user_id=USER, persona_id=PERSONA, query="What was the name of my dog?"
    )
    assert chains
    # One batched call: query + candidate contents together.
    assert len(emb.calls) == 1
    assert len(emb.calls[0]) == 2


def test_make_semantic_embedder_gates_on_mode_and_key() -> None:
    off = Settings(embeddings_mode="hash", litellm_api_key_openai="sk-x")
    assert make_semantic_embedder(off) is None
    no_key = Settings(
        embeddings_mode="semantic", embeddings_api_key="", litellm_api_key_openai=""
    )
    assert make_semantic_embedder(no_key) is None
    on = Settings(embeddings_mode="semantic", litellm_api_key_openai="sk-x")
    assert make_semantic_embedder(on) is not None
    dedicated = Settings(embeddings_mode="semantic", embeddings_api_key="sk-y")
    assert make_semantic_embedder(dedicated) is not None


def test_hash_embed_contract_unchanged() -> None:
    # The zero-config default stays: deterministic, normalized, EMBED_DIM-long.
    v = embed("Мою собаку зовут Мэйпл")
    assert len(v) == EMBED_DIM
    assert sum(x * x for x in v) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_append_event_stores_provided_semantic_vector_and_model() -> None:
    # Phase 3a write path: a precomputed semantic vector wins over the hash
    # vector, and its embedding-space marker rides along for the ANN filter.
    store = InMemoryStore()
    vec = [1.0] + [0.0] * (EMBED_DIM - 1)
    ev = await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="semantic write path",
        embedding=vec,
        embedding_model="text-embedding-3-small",
    )
    assert ev.__dict__["_embedding"] == vec
    assert ev.__dict__["_embedding_model"] == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_append_event_without_vector_marks_hash_space() -> None:
    store = InMemoryStore()
    ev = await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="hash write path",
    )
    assert len(ev.__dict__["_embedding"]) == EMBED_DIM
    assert ev.__dict__["_embedding_model"] is None


@pytest.mark.asyncio
async def test_cache_is_shared_across_instances_of_same_model() -> None:
    # BYOK embedders are per-request instances; the process-wide cache keyed by
    # (model, text) means a second instance of the SAME model reuses vectors.
    model = f"test-{uuid.uuid4().hex}"
    a = _CountingEmbedder(model=model)
    b = _CountingEmbedder(model=model)
    await a.embed_batch(["shared text"])
    out = await b.embed_batch(["shared text"])
    assert out is not None
    assert a.calls == [["shared text"]]
    assert b.calls == []  # served entirely from the shared cache


@pytest.mark.asyncio
async def test_cache_never_mixes_models() -> None:
    # Same text, different models → different spaces → both instances call out.
    a = _CountingEmbedder()
    b = _CountingEmbedder()
    await a.embed_batch(["same text"])
    await b.embed_batch(["same text"])
    assert a.calls == [["same text"]]
    assert b.calls == [["same text"]]


@pytest.mark.asyncio
async def test_recall_chains_prefers_request_embedder_over_store_default() -> None:
    store_emb = _CountingEmbedder()
    byok_emb = _CountingEmbedder()
    store = InMemoryStore(semantic_embedder=store_emb)
    await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    chains = await store.recall_chains(
        user_id=USER, persona_id=PERSONA, query="dog name?", embedder=byok_emb
    )
    assert chains
    assert len(byok_emb.calls) == 1  # the BYOK override embedded the batch
    assert store_emb.calls == []  # the env-configured default never ran
