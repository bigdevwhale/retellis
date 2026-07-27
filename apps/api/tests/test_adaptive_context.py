"""Adaptive context assembly (Phase 1c): window trimming, recall depth, note.

Pure-function tests — no store, no LLM. The invariants: chitchat shrinks the
window back to the default and recalls less; an emotionally loaded stretch is
kept intact (up to the fetched max) and recalls more; the emotional note fires
only on intense recent user messages and never performs empathy (no "I feel").
"""

from __future__ import annotations

from ai_companion_contracts import Event, EventRole

from ai_companion_api.memory.adaptive import (
    DEFAULT_WINDOW,
    MAX_WINDOW,
    RECALL_K_BASE,
    RECALL_K_MAX,
    RECALL_K_MIN,
    emotional_context_note,
    recall_k,
    trim_recent_window,
)


def _evt(
    i: int,
    *,
    role: EventRole = EventRole.user,
    intensity: float = 0.0,
    short_term: float = 0.0,
    tags: list[str] | None = None,
) -> Event:
    return Event(
        id=f"e{i}",
        user_id="u",
        persona_id="p",
        role=role,
        content=f"msg {i}",
        emotional_intensity=intensity,
        short_term_salience=short_term,
        emotion_tags=tags or [],
    )


# --- trim_recent_window -------------------------------------------------------


def test_trim_keeps_all_when_short() -> None:
    evts = [_evt(i) for i in range(4)]
    assert trim_recent_window(evts) == evts


def test_trim_shrinks_neutral_history_to_default() -> None:
    evts = [_evt(i) for i in range(MAX_WINDOW)]
    out = trim_recent_window(evts)
    assert len(out) == DEFAULT_WINDOW
    assert out == evts[-DEFAULT_WINDOW:]


def test_trim_extends_through_loaded_stretch() -> None:
    # The 3 events just before the default boundary are emotionally loaded —
    # the window extends backward through them and stops at the first neutral.
    evts = [_evt(i) for i in range(MAX_WINDOW)]
    boundary = MAX_WINDOW - DEFAULT_WINDOW
    for i in range(boundary - 3, boundary):
        evts[i] = _evt(i, intensity=0.8)
    out = trim_recent_window(evts)
    assert len(out) == DEFAULT_WINDOW + 3
    assert out[0].id == f"e{boundary - 3}"


def test_trim_never_exceeds_fetched_max() -> None:
    evts = [_evt(i, intensity=0.9) for i in range(MAX_WINDOW)]
    out = trim_recent_window(evts)
    assert len(out) == MAX_WINDOW


# --- recall_k -----------------------------------------------------------------


def test_recall_k_min_for_trivial_message() -> None:
    assert recall_k("ok", []) == RECALL_K_MIN
    assert recall_k("привет", []) == RECALL_K_MIN


def test_recall_k_base_for_ordinary_message() -> None:
    assert recall_k("I went to the store and bought some bread today", []) == RECALL_K_BASE


def test_recall_k_max_for_intense_message_in_loaded_window() -> None:
    recent = [_evt(i, intensity=0.8) for i in range(3)]
    k = recall_k("I am so scared and lost since my mother died", recent)
    assert k == RECALL_K_MAX


def test_recall_k_bumps_on_loaded_recent_window_alone() -> None:
    recent = [_evt(i, intensity=0.8) for i in range(3)]
    k = recall_k("what should I cook for dinner tonight maybe", recent)
    assert k == RECALL_K_BASE + 1


# --- emotional_context_note ---------------------------------------------------


def test_note_absent_for_calm_window() -> None:
    recent = [_evt(i, intensity=0.2) for i in range(4)]
    assert emotional_context_note(recent) is None


def test_note_absent_without_user_events() -> None:
    recent = [_evt(i, role=EventRole.assistant, intensity=0.9) for i in range(3)]
    assert emotional_context_note(recent) is None


def test_note_fires_on_intense_recent_user_messages() -> None:
    recent = [
        _evt(0, intensity=0.1),
        _evt(1, intensity=0.8, tags=["grief", "lonely"]),
        _evt(2, role=EventRole.assistant),
    ]
    note = emotional_context_note(recent)
    assert note is not None
    assert note["role"] == "system"
    assert "grief" in note["content"] and "lonely" in note["content"]
    # Honest limits: disclosed as auto-classified, no performed empathy.
    assert "auto-classified" in note["content"]
    assert "I feel" not in note["content"]
    assert "do not claim feelings of your own" in note["content"]


def test_note_uses_only_the_last_three_user_messages() -> None:
    # An intense message 4+ user-turns ago no longer drives the note.
    recent = [_evt(0, intensity=0.9, tags=["panic"])] + [
        _evt(i, intensity=0.1) for i in range(1, 5)
    ]
    assert emotional_context_note(recent) is None
