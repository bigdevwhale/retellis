"""Deterministic salience scorer — the FALLBACK path.

The primary path is ``salience_llm.judge_salience``: the model that just served
the turn rates the user's message for emotional salience + extracts emotion
tags. That works across languages (EN/RU/…) without a hand-maintained lexicon
and without an extra provider key (it reuses the serving candidate's key).

This heuristic is the zero-config fallback used when no real provider served the
turn (mock stand-in), the judge call fails, or the reply is unparseable. It is
deliberately small and English-biased — good enough to keep the timeline from
showing 0.0 for every event when running fully offline, but not a substitute for
the LLM judge. Scores are in [0, 1]; emotion tags are the matched lexicon words
(top 3, order-preserving, unique).

Tokenization is Unicode-aware (``embeddings.tokenize``) so the length factor
still fires for Cyrillic text — but the lexicon matches are English only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .embeddings import tokenize


@dataclass(frozen=True)
class SalienceScore:
    """Multi-dimensional salience used to rank and route events.

    - salience: long-term recall weight — how much the moment should matter
      months later.
    - short_term_salience: boost for the immediate next few turns; decays
      quickly and mainly steers the assistant's working memory window.
    - emotional_intensity: acute emotional charge in the message; drives tone
      calibration and can override pure semantic similarity when recalling.
    - factual_novelty: how much durable *factual* content the message carries
      ("my name is Marat", "I moved to Berlin") regardless of emotional charge.
      Gates memory extraction alongside ``salience`` (P0 #3) so identity facts
      stated calmly are not lost forever; not persisted on events.
    - emotion_tags: up to 3 lowercase single-word labels (in the user's
      language when produced by the LLM judge).
    """

    salience: float = 0.0
    short_term_salience: float = 0.0
    emotional_intensity: float = 0.0
    factual_novelty: float = 0.0
    emotion_tags: list[str] | None = None

    def __post_init__(self) -> None:
        # dataclass is frozen, so validate in place by reassigning through
        # object.__setattr__. Values are clamped to [0, 1]; tags are normalized.
        object.__setattr__(self, "salience", _clamp01(self.salience))
        object.__setattr__(self, "short_term_salience", _clamp01(self.short_term_salience))
        object.__setattr__(self, "emotional_intensity", _clamp01(self.emotional_intensity))
        object.__setattr__(self, "factual_novelty", _clamp01(self.factual_novelty))
        tags = self.emotion_tags
        if tags is None:
            tags = []
        else:
            tags = [t.lower().strip() for t in tags if t.strip()][:3]
        object.__setattr__(self, "emotion_tags", tags)


# A small, hand-curated English lexicon — not a clinical instrument. Only used
# when the LLM judge is unavailable (offline/mock/error). Do not extend this for
# other languages — add language coverage via the LLM judge, not here.
_EMOTION = {
    "sad",
    "grief",
    "grieving",
    "lost",
    "loss",
    "death",
    "died",
    "dies",
    "die",
    "cry",
    "crying",
    "afraid",
    "scared",
    "fear",
    "anxious",
    "anxiety",
    "panic",
    "happy",
    "happiness",
    "joy",
    "joyful",
    "love",
    "loved",
    "loving",
    "angry",
    "anger",
    "rage",
    "shame",
    "ashamed",
    "guilt",
    "guilty",
    "lonely",
    "alone",
    "hope",
    "hopeful",
    "relief",
    "relieved",
    "proud",
    "hurt",
    "pain",
    "painful",
    "broken",
    "numb",
    "empty",
    "exhausted",
    "overwhelmed",
    "stuck",
    "paralyzed",
    "frozen",
    "grateful",
}

# Anchor nouns: people, relations, life transitions, proper nouns → bias recall
# toward concrete, citable events (the things a companion should be able to recall).
_ANCHOR = {
    "dog",
    "cat",
    "puppy",
    "mother",
    "mom",
    "father",
    "dad",
    "daughter",
    "son",
    "sister",
    "brother",
    "wife",
    "husband",
    "partner",
    "girlfriend",
    "boyfriend",
    "boss",
    "job",
    "work",
    "report",
    "team",
    "lead",
    "manager",
    "promotion",
    "move",
    "moved",
    "moving",
    "married",
    "marriage",
    "wedding",
    "divorce",
    "fired",
    "laid",
    "quit",
    "resigned",
    "promoted",
    "passed",
    "diagnosis",
    "surgery",
    "hospital",
    "interview",
    "deadline",
    "exam",
    "graduation",
    "therapist",
    "therapy",
    "session",
}

_INTENSIFIER = {
    "really",
    "very",
    "extremely",
    "incredibly",
    "so",
    "absolutely",
    "completely",
    "totally",
    "utterly",
    "deeply",
    "intensely",
    "terribly",
    "awfully",
    "fucking",
    "damn",
}


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _has_non_latin(text: str) -> bool:
    """True when the text carries letters outside Latin-1 (Cyrillic, CJK, …) —
    the English lexicons are structurally blind to it."""
    return any(ord(c) > 0x024F and c.isalpha() for c in text)


def extract_emotion_tags(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in tokenize(text):
        if t in _EMOTION and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 3:
            break
    return out


def score_salience(text: str) -> SalienceScore:
    toks = tokenize(text)
    if not toks:
        return SalienceScore()
    n = len(toks)
    emo = sum(1 for t in toks if t in _EMOTION)
    has_anchor = any(t in _ANCHOR for t in toks)
    has_digit = any(c.isdigit() for c in text)
    has_intensifier = any(t in _INTENSIFIER for t in toks)
    density = emo / n
    length_factor = min(n / 40.0, 1.0)

    # P2: language-aware fallback. The lexicons are English-only, so for
    # non-Latin text (Cyrillic, …) density/anchor/intensifier are structurally
    # zero and every offline event used to score ~0.1 — permanently poisoning
    # the ranking base (decay starts from it). When the lexicon demonstrably
    # can't see the text, lean on the language-neutral signals (length,
    # digits) with honest mid-range weights instead of asserting "trivial".
    lexicon_blind = emo == 0 and not has_anchor and _has_non_latin(text)
    if lexicon_blind:
        blind_base = 0.30 * length_factor + 0.05 * (1.0 if has_digit else 0.0)
        return SalienceScore(
            salience=0.10 + blind_base,
            short_term_salience=0.20 + blind_base,
            # Unknown ≠ intense: don't fabricate emotional charge the
            # heuristic cannot actually see.
            emotional_intensity=0.10,
            factual_novelty=0.10 + blind_base,
            emotion_tags=[],
        )

    # Long-term salience: emotional content + concrete anchors + modest length.
    salience = (
        0.10
        + 0.45 * density
        + 0.10 * (1.0 if has_anchor else 0.0)
        + 0.15 * length_factor
        + 0.05 * (1.0 if has_digit else 0.0)
    )

    # Short-term salience: any emotional signal, even weak, should nudge the
    # next few turns. Higher base, lower ceiling than long-term salience.
    short_term_salience = 0.20 + 0.40 * density + 0.20 * length_factor
    if has_intensifier:
        short_term_salience += 0.15

    # Emotional intensity: density of emotion words + intensifiers + anchor
    # events (e.g., "my dad died" is both anchored and intense).
    emotional_intensity = (
        0.10
        + 0.55 * density
        + 0.15 * (1.0 if has_intensifier else 0.0)
        + 0.15 * (1.0 if has_anchor else 0.0)
    )

    # Factual novelty: concrete anchors + numbers + some length suggest a
    # durable fact even with zero emotional charge ("I moved to Berlin").
    # The primary signal is the LLM judge; this only keeps the offline path
    # from scoring flat zero.
    factual_novelty = (
        0.05
        + 0.35 * (1.0 if has_anchor else 0.0)
        + 0.15 * (1.0 if has_digit else 0.0)
        + 0.15 * length_factor
    )

    return SalienceScore(
        salience=salience,
        short_term_salience=short_term_salience,
        emotional_intensity=emotional_intensity,
        factual_novelty=factual_novelty,
        emotion_tags=extract_emotion_tags(text),
    )


__all__ = ["extract_emotion_tags", "score_salience", "SalienceScore"]
