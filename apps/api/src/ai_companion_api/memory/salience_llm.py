"""LLM-judge salience scorer — the primary path when a real provider served the turn.

Asks the model that just served the turn to rate the user's message for emotional
salience (how worth recalling later) and to extract up to 3 emotion tags. Returns
a ``SalienceScore`` parsed from a JSON-only reply, or ``None`` on any failure
(mock stand-in, no ``complete`` method, network error, unparseable output) — the
caller falls back to the deterministic heuristic in ``salience.py``.

This is the design the plan calls "LLM-judge salience on write". It works across
languages (EN/RU/…) without a hand-maintained lexicon, and reuses the serving
candidate's key+model so it costs one extra LLM call per turn on the user's own
provider (no separate judge key, no server-side key required).

"Disclose, don't perform": the judge *classifies*, it does not perform empathy.
The system prompt forbids empathetic language and demands JSON only, so the
model never simulates affect toward the user's message.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from ..observability import redact
from .salience import SalienceScore

if TYPE_CHECKING:
    from ..llm.types import LlmAdapter

logger = logging.getLogger(__name__)

# Tolerant JSON extraction: models sometimes wrap the object in prose or fences.
_JSON_OBJECT = re.compile(r"\{[^{}]*\}", re.DOTALL)

# Bound the judge prompt — salience is about the gist, not the tail.
_MAX_TEXT = 4000

_JUDGE_SYSTEM = (
    "You are a memory salience classifier for an AI companion app. "
    'Given a user\'s chat message, output ONLY a JSON object: '
    '{"salience": <float>, "short_term_salience": <float>, "emotional_intensity": <float>, '
    '"factual_novelty": <float>, "emotion_tags": [<strings>]}.'
    "\n- salience: 0.0 to 1.0 — how emotionally significant or worth recalling later this message is. "
    "0.0 = trivial chitchat ('hi', 'ok'); 1.0 = a major life event, relationship shift, or strong "
    "emotional content. Use the whole range."
    "\n- short_term_salience: 0.0 to 1.0 — how much this message should influence the *immediate* "
    "next few replies, even if it wouldn't be recalled months later. 'ok' = 0.0; a current worry "
    "or urgent feeling = high."
    "\n- emotional_intensity: 0.0 to 1.0 — the acute emotional charge *right now*. Calm, factual "
    "statements = low; raw distress, elation, or anger = high."
    "\n- factual_novelty: 0.0 to 1.0 — how much durable factual content about the user's life the "
    "message carries, INDEPENDENT of emotion. A calm 'by the way, I moved to Berlin' or 'my name "
    "is Marat' = high; opinions, small talk, reactions = low."
    "\n- emotion_tags: up to 3 lowercase single-word emotion labels; use [] if none."
    "\n- LANGUAGE: write the tag WORDS in the SAME language the user wrote in. Detect it from the "
    'message (English → English tags like ["tired","lonely"]; Russian → Russian tags like '
    '["усталость","одиночество"]; etc.). Do NOT translate tags to English when the user wrote in '
    "another language."
    "\nDo NOT acknowledge, restate, or empathize with the message. Do NOT perform empathy. Output "
    "JSON only — no prose, no markdown, no code fences."
)


async def judge_salience(
    adapter: LlmAdapter, model: str, text: str
) -> SalienceScore | None:
    """Return a ``SalienceScore`` judged by the serving model, or ``None``.

    ``None`` means "fall back to the heuristic" — used when the adapter is the
    mock stand-in, lacks a ``complete`` method, the judge call fails, or the
    reply is unparseable. Never raises.
    """
    if not text.strip() or getattr(adapter, "provider_kind", "") == "mock":
        return None
    if not hasattr(adapter, "complete"):
        return None
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": text[:_MAX_TEXT]},
    ]
    try:
        raw = await adapter.complete(messages, model)
    except Exception as exc:  # network / provider error → heuristic fallback
        logger.debug(
            "salience judge call failed (heuristic fallback): %s: %s",
            type(exc).__name__,
            redact(str(exc)),
        )
        return None
    parsed = _parse(raw)
    if parsed is None:
        logger.debug(
            "salience judge reply unparseable (heuristic fallback): %s",
            redact((raw or "")[:200]),
        )
        return None
    return parsed


def _parse(raw: str) -> SalienceScore | None:
    if not raw:
        return None
    obj = _to_json(raw)
    if obj is None:
        return None
    try:
        salience = float(obj.get("salience", 0.0))
        short_term_salience = float(obj.get("short_term_salience", 0.0))
        emotional_intensity = float(obj.get("emotional_intensity", 0.0))
        # Older prompts / stubborn models may omit the P0 dimension — default
        # 0.0 keeps the pre-P0 gate behavior (salience alone decides).
        factual_novelty = float(obj.get("factual_novelty", 0.0))
    except (TypeError, ValueError):
        return None
    tags = obj.get("emotion_tags", []) or []
    if not isinstance(tags, list):
        return None
    return SalienceScore(
        salience=salience,
        short_term_salience=short_term_salience,
        emotional_intensity=emotional_intensity,
        factual_novelty=factual_novelty,
        emotion_tags=[str(t) for t in tags],
    )


def _to_json(raw: str) -> dict | None:  # type: ignore[type-arg]
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_OBJECT.search(raw)
        if m is None:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


__all__ = ["judge_salience"]
