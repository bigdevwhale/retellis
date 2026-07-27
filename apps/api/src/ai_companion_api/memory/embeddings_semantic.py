"""Semantic embeddings — the opt-in upgrade over the feature-hashing embedder.

``SemanticEmbedder`` batches texts through ``litellm.aembedding`` (one API call
per batch) with ``dimensions=EMBED_DIM`` truncation so vectors stay compatible
with the ``vector(384)`` column and the ``cosine`` contract (L2-normalized →
dot product). It is used ONLY at recall time: the query + every candidate's
content are embedded in the same call, so ranking always compares vectors from
one embedding space (no hash-vs-semantic mixing).

Failure model: ``embed_batch`` never raises — any provider/network/parse error
returns ``None`` and the caller falls back to the hash embedder, so recall
degrades gracefully instead of breaking a turn. Errors are logged redacted.

Cost model: an in-process LRU cache (keyed by text hash) means only *new*
texts hit the API each turn — steady-state cost is the query + the turn's new
events, not the whole history. Enabled via ``EMBEDDINGS_MODE=semantic`` + an
API key (``EMBEDDINGS_API_KEY`` or the existing ``LITELLM_API_KEY_OPENAI``).

litellm is imported lazily inside the call so this module stays importable
without it (the eval gate imports the memory package litellm-free; it never
constructs an embedder).
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from collections.abc import Sequence
from math import sqrt
from typing import TYPE_CHECKING

from ai_companion_contracts import Event, EventChain

from ..observability import redact
from .embeddings import EMBED_DIM
from .recall import MEMORY_K_RELEVANT, MEMORY_K_STABLE, rank_and_chain, rank_memories

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)

# LRU entries are ~384 floats each; 4096 entries keeps the cache well under
# ~50MB while covering a long conversation history.
_CACHE_MAX = 4096

# Bound each text sent to the embedding API — recall is about the gist.
_MAX_TEXT = 2000

# Process-wide vector cache, keyed by ``(model, text_hash)``. Shared across
# embedder instances because BYOK embedders are per-request (a fresh instance
# every turn — an instance-local cache would never hit) and because vectors
# from different models must never collide (they are different spaces). The
# vector of a text is a pure function of (model, text), so a cross-user cache
# hit reveals nothing: producing the key already requires knowing the text.
_cache: OrderedDict[tuple[str, bytes], list[float]] = OrderedDict()


def _text_key(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


def _normalize(vec: list[float]) -> list[float]:
    norm = sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _remember(key: tuple[str, bytes], vec: list[float]) -> None:
    _cache[key] = vec
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


class SemanticEmbedder:
    """Batched semantic embedding over the shared process-wide LRU cache.

    Instances are cheap: the env-configured embedder lives on the store for the
    process lifetime; a BYOK embedder is constructed per turn around the user's
    ECDH-sealed key and dropped after recall (the key ``str`` is request-scoped;
    the same honest-zeroize disclosure as the chat call applies — see CLAUDE.md).
    """

    def __init__(self, *, model: str, api_key: str, base_url: str | None = None) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url

    @property
    def model(self) -> str:
        """The embedding model id — persisted as ``events.embedding_model`` on
        the write path and matched by the ANN recall prefilter."""
        return self._model

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]] | None:
        """Embed ``texts`` (cache-aware). Returns ``None`` on any failure —
        the caller falls back to the hash embedder. Never raises."""
        bounded = [t[:_MAX_TEXT] for t in texts]
        keys = [(self._model, _text_key(t)) for t in bounded]
        missing = [
            (i, t) for i, (k, t) in enumerate(zip(keys, bounded, strict=True)) if k not in _cache
        ]
        if missing:
            fetched = await self._call([t for _, t in missing])
            if fetched is None or len(fetched) != len(missing):
                return None
            for (i, _), vec in zip(missing, fetched, strict=True):
                _remember(keys[i], _normalize(vec))
        out: list[list[float]] = []
        for k in keys:
            vec = _cache.get(k)
            if vec is None:  # defensive — a failed fetch already returned None
                return None
            _cache.move_to_end(k)
            out.append(vec)
        return out

    async def _call(self, texts: list[str]) -> list[list[float]] | None:
        """One litellm embedding call. Split out so tests can stub it."""
        try:
            import litellm  # lazy — keeps the module importable without litellm

            kwargs: dict = {}  # type: ignore[type-arg]
            if self._base_url:
                kwargs["api_base"] = self._base_url
            resp = await litellm.aembedding(
                model=self._model,
                input=texts,
                dimensions=EMBED_DIM,
                api_key=self._api_key,
                **kwargs,
            )
            data = sorted(resp.data, key=lambda d: d["index"] if isinstance(d, dict) else d.index)
            vecs = [d["embedding"] if isinstance(d, dict) else d.embedding for d in data]
            if any(len(v) != EMBED_DIM for v in vecs):
                logger.warning(
                    "semantic embedding returned wrong dimension (hash fallback): model=%s",
                    self._model,
                )
                return None
            return [list(map(float, v)) for v in vecs]
        except Exception as exc:  # provider/network/parse error → hash fallback
            logger.warning(
                "semantic embedding call failed (hash fallback): %s: %s",
                type(exc).__name__,
                redact(str(exc)),
            )
            return None

def make_semantic_embedder(settings: Settings) -> SemanticEmbedder | None:
    """Build the embedder when semantic mode is on and a key exists, else None
    (recall stays on the zero-config hash path)."""
    if settings.embeddings_mode != "semantic":
        return None
    api_key = settings.embeddings_api_key or settings.litellm_api_key_openai
    if not api_key:
        logger.warning(
            "EMBEDDINGS_MODE=semantic but no EMBEDDINGS_API_KEY/LITELLM_API_KEY_OPENAI — "
            "recall stays on the hash embedder"
        )
        return None
    return SemanticEmbedder(model=settings.embeddings_model, api_key=api_key)


async def rank_chains_semantic(
    embedder: SemanticEmbedder,
    candidates: Sequence[Event],
    query: str,
    k: int = 3,
) -> list[EventChain] | None:
    """Rank with semantic vectors (query + candidates from ONE batch call).
    Returns ``None`` on embedding failure — the caller falls back to the pure
    hash-embedder ``rank_and_chain``."""
    if not candidates:
        return []
    vecs = await embedder.embed_batch([query, *[c.content for c in candidates]])
    if vecs is None:
        return None
    return rank_and_chain(candidates, query, k, query_vec=vecs[0], cand_vecs=vecs[1:])


async def rank_memories_semantic(
    embedder: SemanticEmbedder,
    memories: Sequence[object],
    query: str,
    *,
    k_stable: int = MEMORY_K_STABLE,
    k_relevant: int = MEMORY_K_RELEVANT,
) -> list[object] | None:
    """Select context-slot memories with semantic vectors (P0 #1) — query +
    every memory content from ONE batched call, so ranking never mixes
    embedding spaces. Memory contents change rarely, so the shared LRU cache
    makes the steady-state cost just the query embedding. Returns ``None`` on
    embedding failure — the caller falls back to the pure hash-embedder
    ``rank_memories``."""
    if not memories:
        return []
    vecs = await embedder.embed_batch(
        [query, *[str(getattr(m, "content", "")) for m in memories]]
    )
    if vecs is None:
        return None
    return rank_memories(
        memories,
        query,
        k_stable=k_stable,
        k_relevant=k_relevant,
        query_vec=vecs[0],
        mem_vecs=vecs[1:],
    )


__all__ = [
    "SemanticEmbedder",
    "make_semantic_embedder",
    "rank_chains_semantic",
    "rank_memories_semantic",
]
