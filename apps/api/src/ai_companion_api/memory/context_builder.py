"""Assembles the message list handed to the LLM adapter.

Per turn the context is (PLAN §6)::

    [persona_block, salient_chains..., recent_window..., current_user_message]

The persona block is deterministic and injected every turn so the companion's
voice cannot drift as the event chain grows. ``salient_chains`` and
``recent_window`` are rendered upstream — the LLM router fills them from the
event store (``recall_chains`` → ``chains_to_messages`` for the chains,
``recent_window`` → ``_events_to_window`` for the window, family-scoped) and
passes them in already-formatted. This builder is therefore a thin,
side-effect-free assembler; the orchestration (store I/O, family scope, error
handling) lives in the router where the request scope is. Both slots are
optional and best-effort: a recall failure degrades to ``[persona_block,
current_msg]`` and never breaks the turn.

``persona_prompt`` / ``persona_tone`` (Phase 3+) let a custom persona — which
has no builtin-registry entry — actually shape the model: the client sends its
composed specialization/character/approach prompt and tone sliders, and the
block is built from those instead of the generic fallback (see
``persona_block``).
"""

from __future__ import annotations

from .persona_block import build_persona_block


def build_context(
    *,
    persona_id: str,
    message: str,
    recent_window: list[dict[str, str]] | None = None,
    salient_chains: list[dict[str, str]] | None = None,
    persona_prompt: str | None = None,
    persona_tone: dict[str, float] | None = None,
    emotional_note: dict[str, str] | None = None,
    salient_memories: dict[str, str] | None = None,
    session_bridge: dict[str, str] | None = None,
    relationship_note: dict[str, str] | None = None,
    open_loops: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": build_persona_block(persona_id, prompt=persona_prompt, tone=persona_tone),
        }
    ]

    # Filled by the router from the event store (Phase 3). All slots are
    # optional — when ``memory_on`` is off or recall fails, they stay empty and
    # the turn proceeds on the persona block + current message alone.
    # P1 (relationship note): the slowly-evolving "we have history" carrier —
    # relationship duration, persistent threads, learned communication
    # preferences. Right after the persona block: it frames everything below
    # the way the static persona frames the voice.
    if relationship_note:
        messages.append(relationship_note)
    # Phase 2b: the distilled long-term layer (atomic memories + episode
    # summaries) comes right after the persona block — stable knowledge frames
    # the episodic chains and the live window that follow.
    if salient_memories:
        messages.append(salient_memories)
    if salient_chains:
        messages.extend(salient_chains)
    # P1 (open loops): unresolved threads the companion may proactively pick
    # up ("how did the interview go?"). After recall, before the live thread.
    if open_loops:
        messages.append(open_loops)
    # P0 #4 (session bridge): on the first turn of a NEW conversation the
    # recent window is empty and the retrieval query is usually a greeting —
    # the weakest possible context at the exact moment continuity matters
    # most. The router passes a one-line factual summary of the previous
    # conversation ("Your previous conversation (5 days ago): …") so the
    # companion can pick the thread back up instead of starting cold.
    if session_bridge:
        messages.append(session_bridge)
    # Adaptive layer (Phase 1c): a factual, auto-classified note about the
    # user's recent emotional state (``adaptive.emotional_context_note``).
    # Sits between recall and the recent window: it frames the live thread,
    # not the long-term memories.
    if emotional_note:
        messages.append(emotional_note)
    if recent_window:
        messages.extend(recent_window)

    messages.append({"role": "user", "content": message})
    return messages
