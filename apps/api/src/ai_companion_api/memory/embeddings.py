"""Deterministic text embeddings (zero-config, no API call).

A 384-dimensional signed feature-hashing embedding over unigrams + bigrams,
L2-normalized so cosine similarity is a plain dot product. This is **not** a
semantic embedder — it captures lexical overlap, which is enough for personal
event-chain recall at MVP scale (a few hundred events) and keeps ``docker
compose up`` working with no OpenAI key. Swapping in ``litellm.embedding`` is a
post-MVP upgrade; the ``EMBED_DIM`` constant and the ``embed``/``cosine``
contract stay stable. See README.
"""

from __future__ import annotations

import hashlib
import re
from math import sqrt

EMBED_DIM = 384  # must match db/models.py::EMBED_DIM

# Unicode letters (any script — covers Latin + Cyrillic for the EN/RU app),
# with internal apostrophes preserved so "don't" / "l'école" stay one token.
# ASCII-only matching silently zeroed salience + embeddings for Russian text
# (Cyrillic produced no tokens → score_salience returned 0.0 and the embedder
# emitted an all-zero vector, breaking recall for non-English chats).
_TOKEN = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _hash(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def embed(text: str) -> list[float]:
    toks = tokenize(text)
    feats = list(toks) + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
    vec = [0.0] * EMBED_DIM
    for f in feats:
        idx = _hash(f) % EMBED_DIM
        sign = 1.0 if (_hash(f + "|sign|") % 2 == 0) else -1.0
        vec[idx] += sign
    norm = sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine for L2-normalized vectors == dot product. Clamped to [0, 1]."""
    sim = sum(x * y for x, y in zip(a, b, strict=False))
    return max(0.0, min(1.0, sim))


__all__ = ["EMBED_DIM", "cosine", "embed", "tokenize"]
