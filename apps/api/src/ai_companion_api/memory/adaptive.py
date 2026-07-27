"""Adaptive context assembly — sizes the context to the emotional moment.

Pure functions (no I/O — eval-gate importable, litellm-free) that use the
multi-dimensional salience on events (``short_term_salience`` /
``emotional_intensity``, Phase 1b) to decide, per turn:

- ``trim_recent_window`` — how much of the recent window to keep. The router
  fetches ``MAX_WINDOW`` events in one query; an emotionally loaded stretch is
  kept intact (up to the max) instead of being cut mid-thread, while trivial
  chitchat shrinks back to the default so tokens aren't wasted.
- ``recall_k`` — how many salient chains to recall. The companion leans on
  memory during emotional moments (more chains) and recalls less for trivial
  one-liners.
- ``emotional_context_note`` — an optional, factual system note summarizing
  the user's recent emotion tags + intensity. "Disclose, don't perform": the
  note is labelled as classifier output and instructs continuity, never
  affect. It never claims the companion feels anything.

All thresholds are deliberately simple constants — this is a deterministic
heuristic layer, not a model. It degrades to the fixed-size Phase 3 behavior
when events carry no dimension scores (all zeros → defaults).
"""

from __future__ import annotations

from collections.abc import Sequence

from ai_companion_contracts import Event

from .salience import score_salience

# Window sizing. DEFAULT matches the pre-adaptive fixed window; the router
# fetches MAX and this module trims.
DEFAULT_WINDOW = 6
MAX_WINDOW = 12

# An event is "emotionally loaded" when either dimension clears its bar.
_LOADED_INTENSITY = 0.4
_LOADED_SHORT_TERM = 0.5

# Recall depth. BASE matches the pre-adaptive k=3.
RECALL_K_BASE = 3
RECALL_K_MIN = 2
RECALL_K_MAX = 5

# A note is emitted only when recent user messages are intense enough that
# continuity actually matters; below this the persona block alone is enough.
_NOTE_INTENSITY = 0.5


def _is_loaded(e: Event) -> bool:
    return (
        float(e.emotional_intensity) >= _LOADED_INTENSITY
        or float(e.short_term_salience) >= _LOADED_SHORT_TERM
    )


def trim_recent_window(events: Sequence[Event]) -> list[Event]:
    """Trim a ``MAX_WINDOW``-sized fetch down to what this turn needs.

    Keeps the last ``DEFAULT_WINDOW`` events always; older events (up to the
    fetched max) survive only while the stretch stays emotionally loaded —
    walking backward from the default boundary, extension stops at the first
    unloaded event. Order is preserved (oldest → newest, as the store returns).
    """
    events = list(events)
    if len(events) <= DEFAULT_WINDOW:
        return events
    keep_from = len(events) - DEFAULT_WINDOW
    while keep_from > 0 and _is_loaded(events[keep_from - 1]):
        keep_from -= 1
    return events[keep_from:]


def recall_k(message: str, recent: Sequence[Event]) -> int:
    """Recall depth for this turn.

    - Trivial one-liners ("ok", "привет") → ``RECALL_K_MIN``.
    - An emotionally intense current message (heuristic — the LLM judge runs
      post-turn) or a loaded recent window → up to ``RECALL_K_MAX``.
    - Otherwise the Phase 3 default.
    """
    scored = score_salience(message)
    words = len(message.split())
    if words <= 3 and scored.emotional_intensity < _LOADED_INTENSITY:
        return RECALL_K_MIN
    loaded_recent = sum(1 for e in recent if e.role == "user" and _is_loaded(e))
    if scored.emotional_intensity >= _LOADED_INTENSITY and loaded_recent >= 2:
        return RECALL_K_MAX
    if scored.emotional_intensity >= _LOADED_INTENSITY or loaded_recent >= 2:
        return RECALL_K_BASE + 1
    return RECALL_K_BASE


def emotional_context_note(recent: Sequence[Event]) -> dict[str, str] | None:
    """A short, factual system note about the user's recent emotional state.

    Built from user-role events' ``emotion_tags`` + ``emotional_intensity``.
    Returns ``None`` when the recent window isn't intense enough — most turns.
    The wording is classification-only ("disclose, don't perform"): it reports
    what the classifier saw and asks for continuity; it never asserts the
    companion's own feelings and never overstates certainty.
    """
    user_events = [e for e in recent if e.role == "user"]
    if not user_events:
        return None
    # Consider the last few user messages — the *current* emotional stretch.
    tail = user_events[-3:]
    peak = max(float(e.emotional_intensity) for e in tail)
    if peak < _NOTE_INTENSITY:
        return None
    tags: list[str] = []
    for e in tail:
        for t in e.emotion_tags:
            if t not in tags:
                tags.append(t)
    tag_part = f" Detected emotion tags: {', '.join(tags[:5])}." if tags else ""
    return {
        "role": "system",
        "content": (
            "Recent emotional context (auto-classified, may be imperfect): the user's last "
            f"messages carry high emotional intensity.{tag_part} Keep continuity with what "
            "they have shared; do not restart the topic from zero and do not claim feelings "
            "of your own."
        ),
    }


__all__ = [
    "DEFAULT_WINDOW",
    "MAX_WINDOW",
    "RECALL_K_BASE",
    "RECALL_K_MAX",
    "RECALL_K_MIN",
    "emotional_context_note",
    "recall_k",
    "trim_recent_window",
]
