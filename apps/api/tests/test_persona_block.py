"""Tests for the deterministic persona block + tone directives.

The persona block is the empathy differentiator: it must be deterministic and
injected (never reconstructed from memory), so we assert stability and that the
tone sliders actually shape the block. The tone buckets are deliberately coarse
(low / mid / high) — slider wobbles inside a bucket must NOT change the block.
"""

from __future__ import annotations

from ai_companion_api.memory.persona_block import (
    build_persona_block,
    persona_prompt,
    tone_directives,
)

# --- tone_directives: bucket boundaries (0..100) ----------------------------


def test_tone_directives_empty_when_no_tone() -> None:
    assert tone_directives(None) == ""
    assert tone_directives({}) == ""


def test_tone_directives_warmth_buckets() -> None:
    high = tone_directives({"warmth": 90, "direct": 50, "pace": 50})
    mid = tone_directives({"warmth": 55, "direct": 50, "pace": 50})
    low = tone_directives({"warmth": 20, "direct": 50, "pace": 50})
    assert "Lead with warmth" in high
    assert "warm but grounded" in mid.lower()
    assert "measured and neutral" in low


def test_tone_directives_direct_buckets() -> None:
    high = tone_directives({"warmth": 50, "direct": 80, "pace": 50})
    mid = tone_directives({"warmth": 50, "direct": 50, "pace": 50})
    low = tone_directives({"warmth": 50, "direct": 20, "pace": 50})
    assert "concrete next step" in high
    assert "small next step" in mid
    assert "open questions and reflection" in low


def test_tone_directives_pace_buckets() -> None:
    high = tone_directives({"warmth": 50, "direct": 50, "pace": 80})
    mid = tone_directives({"warmth": 50, "direct": 50, "pace": 50})
    low = tone_directives({"warmth": 50, "direct": 50, "pace": 20})
    assert "tight and brief" in high
    assert "mid-length rhythm" in mid
    assert "Slow the pace" in low


def test_tone_directives_bucket_wobble_is_stable() -> None:
    # 40 and 41 sit on either side of the warmth boundary; 39 and 40 don't.
    # Wobbles INSIDE a bucket must yield the identical directive.
    assert tone_directives({"warmth": 41, "direct": 50, "pace": 50}) == tone_directives(
        {"warmth": 69, "direct": 50, "pace": 50}
    )
    # Boundary itself belongs to the lower-or-equal bucket (>= 70 is high).
    assert tone_directives({"warmth": 70, "direct": 50, "pace": 50}) == tone_directives(
        {"warmth": 100, "direct": 50, "pace": 50}
    )


def test_tone_directives_missing_keys_default_to_mid() -> None:
    # A tone dict with a missing axis falls back to 50 (mid), not a crash.
    d = tone_directives({"warmth": 90})  # direct/pace missing
    assert "warmth" in d.lower() or "Lead with warmth" in d
    assert "mid-length rhythm" in d  # pace defaulted to 50 (mid)


def test_tone_directives_is_deterministic_and_prefixed() -> None:
    d = tone_directives({"warmth": 84, "direct": 25, "pace": 40})
    assert d.startswith("Voice — ")
    assert d == tone_directives({"warmth": 84, "direct": 25, "pace": 40})


# --- build_persona_block: builtin personas get tone, customs get generic -----


def test_builtins_append_voice_directive() -> None:
    block = build_persona_block("aria")
    assert block.startswith(persona_prompt("aria"))
    assert "Voice —" in block
    # Aria: warmth 84 (high) → warmth directive; direct 25 (low) → open questions.
    assert "Lead with warmth" in block
    assert "open questions and reflection" in block


def test_nico_high_direct_yields_next_step() -> None:
    block = build_persona_block("nico")  # direct 75 (high)
    assert "concrete next step" in block


def test_lou_low_direct_yields_reflection() -> None:
    block = build_persona_block("lou")  # direct 20 (low)
    assert "open questions and reflection" in block


def test_aria_warmth_is_applied_not_zero() -> None:
    # Regression: the registry used to have key "tone" instead of "warmth" for
    # Aria, which silently zeroed warmth once tone was wired in.
    block = build_persona_block("aria")
    assert "Lead with warmth" in block  # warmth 84 → high bucket
    assert "measured and neutral" not in block  # would be the warmth=0 (low) branch


def test_custom_persona_gets_generic_block_without_directives() -> None:
    block = build_persona_block("does-not-exist")
    assert block == build_persona_block("does-not-exist")  # stable
    assert "Voice —" not in block  # no tone to apply
    assert "calm, honest companion" in block


def test_build_persona_block_is_deterministic() -> None:
    # The empathy differentiator: the same persona_id must yield the identical
    # block every call — the voice cannot drift between turns.
    assert build_persona_block("sam") == build_persona_block("sam")
    # And it still carries the hand-written character prompt.
    assert "Sam, a warm easy friend" in build_persona_block("sam")


def test_language_directive_appended_to_every_block() -> None:
    # The companion must reply in the user's language (e.g. Russian when the
    # user writes Russian) — without this, an English system prompt makes the
    # model default to English. The directive is universal, so it lands on
    # builtins, the generic fallback, AND custom-prompt overrides.
    for persona in ("aria", "sam", "nico", "mira", "lou", "fam", "does-not-exist"):
        block = build_persona_block(persona)
        assert "same language the user writes in" in block, persona
        assert "Russian" in block, persona
    custom = build_persona_block("custom-1", prompt="You are Sage.")
    assert "same language the user writes in" in custom


# --- custom-persona override (prompt + tone come from the request) -----------


def test_prompt_override_appends_tone_directives() -> None:
    block = build_persona_block(
        "custom-1",
        prompt="You are Sage, a gentle sounding board.",
        tone={"warmth": 90, "direct": 30, "pace": 25},
    )
    assert block.startswith("You are Sage, a gentle sounding board.")
    assert "Voice —" in block
    assert "Lead with warmth" in block  # warmth 90 → high
    assert "open questions and reflection" in block  # direct 30 → low


def test_prompt_override_without_tone_is_prompt_plus_lang_directive() -> None:
    block = build_persona_block("custom-1", prompt="You are Sage.")
    # No tone → no "Voice —" directive, but the universal language directive
    # is still appended (it is companion behavior, not persona character).
    assert block.startswith("You are Sage.")
    assert "Voice —" not in block
    assert "same language the user writes in" in block


def test_prompt_override_takes_precedence_over_builtin_id() -> None:
    # Even for a builtin id, an explicit prompt wins — the client sends the
    # override only for custom personas, but the precedence must hold regardless.
    block = build_persona_block("aria", prompt="You are someone else entirely.")
    assert block.startswith("You are someone else entirely.")
    assert "Aria" not in block


# --- fam (family therapist) — honest-limit invariants in the prompt ---------


def test_fam_builtin_is_deterministic_and_appends_voice() -> None:
    block1 = build_persona_block("fam")
    block2 = build_persona_block("fam")
    assert block1 == block2
    assert "Voice —" in block1
    # fam: warmth 82 (high), direct 40 (low-mid), pace 38 (low).
    assert "Lead with warmth" in block1
    # direct 40 → "Offer a frame or a small next step" bucket.
    assert "small next step" in block1
    # pace 38 (low) → "Slow the pace".
    assert "Slow the pace" in block1


def test_fam_prompt_includes_all_honest_limits() -> None:
    """The fam persona must close with the security/honesty disclosures:
    - not a licensed family therapist
    - shared family data is shared with all family members
    - the family owner can see shared data but NOT another member's private
    - disbanding wipes all shared data
    - "Disclose, don't perform."
    """
    p = persona_prompt("fam").lower()
    assert "not a licensed family therapist" in p
    assert "shared family data is shared" in p or "shared with all family members" in p
    assert "owner" in p and "private" in p
    assert "disband" in p
    assert "disclose, don" in p  # closing reminder


def test_fam_prompt_explains_solo_vs_joint_modes() -> None:
    p = persona_prompt("fam").lower()
    # The prompt must distinguish solo 1:1 from joint, and tell the model
    # that private disclosures from any member are NEVER in the joint
    # session's recall — this is the family-feature privacy guarantee.
    assert "solo" in p
    assert "joint" in p
    assert "never" in p  # private is never surfaced in joint
