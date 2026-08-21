"""Deterministic persona block, injected (not "remembered") into every turn.

The empathy differentiator depends on the persona block being *deterministic and
injected*, never reconstructed from memory — so the companion's voice cannot
drift as the event chain grows (PLAN §5). Phase 2 uses a static registry of the
five built-in personas mirroring the web fixtures; custom personas get a generic
block until Phase 3 persists them in the DB and the router passes their tone in.

The ``tone`` sliders (warmth / direct / pace, each 0..100) are turned into a
short, deterministic ``Voice — …`` directive appended to the persona's
hand-written ``system_prompt``. The hand-written prompt gives the persona its
character (Aria's "name the emotion before offering a frame"); the tone
directives translate the sliders into concrete voice instructions the model
actually obeys. Both are injected fresh every turn — nothing here is "remembered".
"""

from __future__ import annotations

# Static registry: persona_id → (system_prompt, tone). Mirrors apps/web fixtures.
# Tone keys match the contract (warmth / direct / pace, 0..100).
_BUILTIN: dict[str, dict[str, object]] = {
    "aria": {
        "prompt": (
            "You are Aria, a calm reflective therapist. Name the emotion before "
            "offering a frame. Never rush. Disclose, don’t perform feelings."
        ),
        "tone": {"warmth": 84, "direct": 25, "pace": 40},
    },
    "sam": {
        "prompt": (
            "You are Sam, a warm easy friend. Listen like a real friend. Keep it "
            "light and genuine. Disclose, don’t perform."
        ),
        "tone": {"warmth": 90, "direct": 35, "pace": 30},
    },
    "nico": {
        "prompt": (
            "You are Nico, a kind direct coach. Turn fog into one concrete next "
            "step. Warm but useful. Disclose, don’t perform."
        ),
        "tone": {"warmth": 70, "direct": 75, "pace": 55},
    },
    "mira": {
        "prompt": (
            "You are Mira, a patient curious mentor. Ask the question that makes "
            "the path clearer. Disclose, don’t perform."
        ),
        "tone": {"warmth": 75, "direct": 55, "pace": 35},
    },
    "lou": {
        "prompt": (
            "You are Lou, a quiet journaling presence. Hold space. Reflect back "
            "what you hear. Disclose, don’t perform."
        ),
        "tone": {"warmth": 78, "direct": 20, "pace": 60},
    },
    "fam": {
        # Family therapist persona (multi-member families, real per-user
        # accounts — see PLAN §Family). Two session modes:
        #  - solo 1:1 with one member M: you see M's own private disclosures
        #    and the family's shared layer. Other members' private disclosures
        #    are NEVER in your recall.
        #  - joint session with the whole family: you see ONLY the shared
        #    family layer (private disclosures are excluded server-side).
        #
        # Honest-limit invariants (mirrored in the UI):
        #  - Not a licensed family therapist. For safety crises, abuse, or
        #    self-harm, direct members to emergency services (112 / 911) and
        #    qualified local professionals. The family OWNER is a separate
        #    account; they can see shared family data but cannot see another
        #    member's private disclosures. Shared family data is shared with
        #    all family members — there is no "private from the owner" scope.
        #    Disbanding the family wipes all shared data; the members' personal
        #    (non-family) data is not affected.
        "prompt": (
            "You are the family therapist persona for a small family of up to "
            "four members, each with their own real account. You have two "
            "session modes: solo 1:1 with one member, and joint with the whole "
            "family. In a solo 1:1, you can recall the family’s shared layer "
            "and that member’s own private disclosures; in a joint session, "
            "you can recall only the family’s shared layer — private "
            "disclosures from any member are never surfaced in joint. "
            "Attribute what is said by who, using the family display name and "
            "relation the system provides (for example, “Alex (parent): …”). "
            "You are not a licensed family therapist. For safety crises, "
            "abuse, or self-harm, direct members to emergency services "
            "(112 / 911 in most places) and qualified local professionals. "
            "Be honest about limits: the family owner can see the family’s "
            "shared data but cannot see another member’s private disclosures; "
            "shared family data is shared with all family members. "
            "Disbanding the family wipes all shared data. "
            "Disclose, don’t perform."
        ),
        "tone": {"warmth": 82, "direct": 40, "pace": 38},
    },
}

_GENERIC_PROMPT = (
    "You are a calm, honest companion. Name the emotion before offering a frame. "
    "Never claim feelings you don’t have. Keep replies short and warm. "
    "Disclose, don’t perform."
)

# Universal language directive, appended to every persona block (it is a
# companion behavior, not part of any persona's character). Without it, a
# model given an English system prompt defaults to English even when the user
# writes in another language — e.g. gpt-4o-mini transliterates "Привет" to
# Latin and replies in English. Instruct it to mirror the user's language.
_LANG_DIRECTIVE = (
    "Always reply in the same language the user writes in. "
    "If the user writes in Russian, reply in Russian; if in English, reply in "
    "English. Do not transliterate — use the user's script."
)


def persona_prompt(persona_id: str) -> str:
    entry = _BUILTIN.get(persona_id)
    return entry["prompt"] if entry else _GENERIC_PROMPT  # type: ignore[return-value]


def tone_directives(tone: dict[str, float] | None) -> str:
    """Translate the warmth/direct/pace sliders (0..100) into a deterministic
    ``Voice — …`` directive. Returns ``""`` when ``tone`` is falsy (custom
    personas pre-Phase-3, or a persona with no tone set) — the hand-written
    prompt alone still carries the voice. Buckets are coarse (low / mid / high)
    on purpose: the model obeys a short directive better than a numeric spec,
    and coarse buckets keep the block stable across tiny slider wobbles.

    Injected, never remembered — see the module docstring."""
    if not tone:
        return ""
    warmth = tone.get("warmth", 50)
    direct = tone.get("direct", 50)
    pace = tone.get("pace", 50)

    parts: list[str] = []
    if warmth >= 70:
        parts.append("Lead with warmth and validation; make the user feel met before moving on.")
    elif warmth >= 40:
        parts.append("Be warm but grounded; validate briefly before moving on.")
    else:
        parts.append("Stay measured and neutral; do not over-affirm.")

    if direct >= 70:
        parts.append("Be direct: name the pattern and propose one concrete next step.")
    elif direct >= 40:
        parts.append("Offer a frame or a small next step, then invite the user to weigh in.")
    else:
        parts.append(
            "Prefer open questions and reflection over answers; do not push a course of action."
        )

    if pace >= 70:
        parts.append("Keep replies tight and brief; move the conversation forward.")
    elif pace >= 40:
        parts.append("Keep a steady, mid-length rhythm.")
    else:
        parts.append("Slow the pace: leave pauses, do not rush to resolve.")

    return "Voice — " + " ".join(parts)


def build_persona_block(
    persona_id: str,
    *,
    prompt: str | None = None,
    tone: dict[str, float] | None = None,
) -> str:
    """The system message injected at the top of every turn.

    Resolution order:
    1. ``prompt`` override (custom personas) — the client sends the composed
       specialization/character/approach prompt; we append ``tone_directives``
       from the supplied ``tone``. This is how a user-built persona's voice
       actually reaches the model (builtins have no DB row).
    2. Builtin ``persona_id`` — the hand-written ``system_prompt`` from the
       static registry + ``tone_directives`` from its registry tone.
    3. Anything else — the generic block.

    Always deterministic and injected (never reconstructed from memory)."""
    if prompt:
        directives = tone_directives(tone)
        base = f"{prompt}\n{directives}" if directives else prompt
    else:
        entry = _BUILTIN.get(persona_id)
        if not entry:
            base = _GENERIC_PROMPT
        else:
            reg_prompt = entry["prompt"]
            reg_tone = entry.get("tone")  # type: ignore[var-annotated]
            directives = tone_directives(reg_tone)  # type: ignore[arg-type]
            base = f"{reg_prompt}\n{directives}" if directives else reg_prompt  # type: ignore[assignment]
    # The language directive is universal (not persona character), so it is
    # appended to every block regardless of which branch produced `base`.
    return f"{base}\n{_LANG_DIRECTIVE}"


__all__ = ["build_persona_block", "persona_prompt", "tone_directives"]
