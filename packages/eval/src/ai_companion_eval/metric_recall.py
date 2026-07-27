"""Recall metric — does a recalled chain contain the expected answer?

Substring match (case-insensitive) of ``expected_recall`` in any event content
of the returned chains. The plan's "embedding cosine >= 0.85 OR substring" is
satisfied by substring here; the deterministic embedder in the API handles the
cosine path during live recall. Returns 1.0 on a hit, 0.0 otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence


def recall_hit(chains: Sequence[object], expected: str) -> float:
    if not expected:
        return 0.0
    needle = expected.lower()
    for ch in chains:
        for e in getattr(ch, "events", []) or []:
            content = getattr(e, "content", "") or ""
            if needle in content.lower():
                return 1.0
    return 0.0


__all__ = ["recall_hit"]