"""Recall — rank events by query relevance + salience + recency, then build
intact chains by walking ``prev_event_id`` backward.

The ranking is pure (no I/O) so the eval gate can import it directly and the
store implementations can delegate to it. ``chains_to_messages`` formats
``EventChain``s into the ``salient_chains`` slot of ``build_context``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ai_companion_contracts import Event, EventChain

from .embeddings import cosine, embed

# --- Phase 2a: time-based salience decay -------------------------------------
# Effective salience declines with age, but emotionally intense moments fade
# slower (half-life stretches with emotional_intensity), and no event decays
# below a floor fraction of its stored salience — a major moment never fully
# vanishes from ranking, it just yields to fresher material. Reinforcement
# (``store.reinforce_events``) counteracts decay for events that keep being
# recalled, so what keeps coming up stays reachable.
_DECAY_BASE_HALF_LIFE_DAYS = 30.0
_DECAY_INTENSITY_EXTRA_DAYS = 90.0
_DECAY_FLOOR = 0.2

# P0: cap each recalled event's content in the rendered context. Recall is
# about the gist; uncapped chains could inject thousands of tokens per turn.
_CHAIN_MAX_CHARS = 300

# P0: memory selection — a few slots always go to the highest-salience rows
# (the stable identity core), the rest to what's relevant to *this* message.
MEMORY_K_STABLE = 2
MEMORY_K_RELEVANT = 4

# P1 (open loops): at most this many unresolved threads are surfaced per turn,
# and only while fresh — a loop untouched for this long is presumed stale
# (resolved off-app or abandoned) and silently stops surfacing.
OPEN_LOOP_MAX = 2
OPEN_LOOP_MAX_AGE_DAYS = 45

# P2: memories decay too — much slower than events (they are already the
# distilled layer) and only inside the RELEVANT-slot scoring; the stable
# identity-core slots keep raw salience so major life facts never age out.
# ``updated_at`` anchors the decay: a memory the extractor keeps touching
# (recurring topic) stays fresh, a fact nobody has mentioned in a year yields
# to fresher material without ever fully vanishing (floor).
_MEM_DECAY_HALF_LIFE_DAYS = 120.0
_MEM_DECAY_FLOOR = 0.25


def effective_memory_salience(m: object, now: datetime) -> float:
    """Stored memory salience with slow time decay from ``updated_at``.
    Rows without a timestamp don't decay."""
    upd = getattr(m, "updated_at", None)
    sal = float(getattr(m, "salience", 0.0))
    if not isinstance(upd, datetime):
        return sal
    age_days = max(0.0, (now - upd).total_seconds() / 86_400.0)
    factor = max(_MEM_DECAY_FLOOR, 0.5 ** (age_days / _MEM_DECAY_HALF_LIFE_DAYS))
    return sal * factor


def relative_time(dt: datetime, now: datetime) -> str:
    """Human relative age ("today", "3 days ago", "2 months ago") for prompt
    rendering. English on purpose — the scaffolding language of every system
    message; the remembered *content* stays in the user's language."""
    days = (now - dt).total_seconds() / 86_400.0
    if days < 1.0:
        return "today"
    if days < 2.0:
        return "yesterday"
    if days < 14.0:
        return f"{int(days)} days ago"
    if days < 61.0:
        return f"{int(days / 7)} weeks ago"
    if days < 365.0 * 2:
        return f"{int(days / 30.4)} months ago"
    return f"{int(days / 365.0)} years ago"


def effective_salience(e: Event, now: datetime) -> float:
    """Stored salience with time decay applied. Events without ``created_at``
    (transient/synthetic) don't decay."""
    created = e.created_at
    if created is None:
        return float(e.salience)
    age_days = max(0.0, (now - created).total_seconds() / 86_400.0)
    half_life = _DECAY_BASE_HALF_LIFE_DAYS + _DECAY_INTENSITY_EXTRA_DAYS * float(
        e.emotional_intensity
    )
    factor = max(_DECAY_FLOOR, 0.5 ** (age_days / half_life))
    return float(e.salience) * factor


def rank_and_chain(
    candidates: Sequence[Event],
    query: str,
    k: int = 3,
    *,
    query_vec: list[float] | None = None,
    cand_vecs: Sequence[list[float]] | None = None,
    now: datetime | None = None,
) -> list[EventChain]:
    """Rank by 0.5·cosine + 0.3·decayed-salience + 0.2·recency, then walk
    prev_event_id backward (≤2 hops) AND forward (≤1 hop, P1) to produce
    intact chains of up to 4 events, oldest→newest. The forward hop matters
    for relationship recall: a salient seed ("my dad died") should carry its
    aftermath — what was said next — not only the lead-up.

    ``query_vec``/``cand_vecs`` are optional precomputed embeddings (the
    semantic path passes vectors from one batched API call so query and
    candidates share an embedding space). When absent, the zero-config hash
    embedder runs in place — this keeps the function pure (no I/O) so the eval
    gate can import it litellm-free. Pass both or neither: mixing a semantic
    query vector with hash candidate vectors would compare across spaces.

    ``now`` anchors the time-based salience decay (tests pass a fixed value;
    default is the current time). Candidates without ``created_at`` don't
    decay, so pre-Phase-2a callers see identical ranking.
    """
    if not candidates:
        return []
    q = query_vec if query_vec is not None else embed(query)
    n = len(candidates)
    now = now or datetime.now(UTC)

    def recency(i: int) -> float:
        return (i + 1) / n  # later appends rank higher

    def cand_vec(i: int, e: Event) -> list[float]:
        return cand_vecs[i] if cand_vecs is not None else embed(e.content)

    scored: list[tuple[float, Event]] = []
    for i, e in enumerate(candidates):
        score = 0.5 * cosine(q, cand_vec(i, e)) + 0.3 * effective_salience(e, now) + 0.2 * recency(i)
        scored.append((score, e))
    scored.sort(key=lambda t: t[0], reverse=True)

    by_id = {e.id: e for e in candidates}
    # Forward index (P1): first child per parent — chains are linear by the
    # per-convo append lock, so "first wins" only matters for synthetic forks.
    child_of: dict[str, Event] = {}
    for e in candidates:
        if e.prev_event_id and e.prev_event_id not in child_of:
            child_of[e.prev_event_id] = e
    chains: list[EventChain] = []
    used: set[str] = set()
    for _, seed in scored:
        if seed.id in used or len(chains) >= k:
            break
        chain: list[Event] = [seed]
        cur = seed
        for _ in range(2):
            pid = cur.prev_event_id
            if not pid or pid not in by_id:
                break
            cur = by_id[pid]
            chain.append(cur)
        chain.reverse()  # oldest → newest
        nxt = child_of.get(seed.id)
        if nxt is not None and nxt.id not in used and nxt.id != seed.id:
            chain.append(nxt)  # the aftermath — one hop past the seed
        for c in chain:
            used.add(c.id)
        chains.append(EventChain(events=chain, salience_sum=sum(float(c.salience) for c in chain)))
    return chains


def chains_to_messages(
    chains: Sequence[EventChain],
    family_members: dict[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Render chains as a system recall block injected before the recent window.

    Kept short and factual (no performed empathy) — the persona block carries
    the voice; this only carries *what the companion has learned*.

    P0 (temporal grounding): when a chain's newest event carries ``created_at``,
    the header names its relative age ("What you know so far (3 months ago):")
    so months-old material never reads as *current* to the model. Events
    without timestamps (eval gate, synthetic) render exactly as before. Each
    event's content is capped at ``_CHAIN_MAX_CHARS`` — recall carries the
    gist; uncapped chains could inject thousands of tokens.

    ``family_members`` is an optional ``{user_id → "Display name (relation)"}``
    map used to attribute user-role rows in a family-scope chain. When a user
    event carries a ``participant_user_id`` that's in the map, the row is
    rendered as ``"{name}: {content}"`` so the family therapist can tell who
    said what in a joint session. Unmapped / assistant rows fall back to the
    default ``_label``.
    """
    msgs: list[dict[str, str]] = []
    fm = family_members or {}
    now = now or datetime.now(UTC)
    for ch in chains:
        facts = [f"{_label(e, fm)}: {e.content[:_CHAIN_MAX_CHARS]}" for e in ch.events]
        body = " | ".join(facts)
        newest = max((e.created_at for e in ch.events if e.created_at is not None), default=None)
        when = f" ({relative_time(newest, now)})" if newest is not None else ""
        msgs.append({"role": "system", "content": f"What you know so far{when}: {body}"})
    return msgs


def rank_memories(
    memories: Sequence[object],
    query: str,
    *,
    k_stable: int = MEMORY_K_STABLE,
    k_relevant: int = MEMORY_K_RELEVANT,
    query_vec: list[float] | None = None,
    mem_vecs: Sequence[list[float]] | None = None,
    now: datetime | None = None,
) -> list[object]:
    """Select which memories deserve this turn's context slots (P0 #1).

    ``list_memories`` orders by ``(salience, updated_at)`` — a query-independent
    ranking that ossifies: after months the same top-N heavyweight memories
    occupy every turn's slots while the contextually relevant fact ("You have a
    dog named Maple") never surfaces. This selector splits the slots:

    - ``k_stable`` slots keep the caller's salience order (the stable identity
      core — major life facts stay present regardless of topic);
    - ``k_relevant`` slots go to the best of the *rest* by
      ``0.65·cosine(query) + 0.2·decayed-salience + 0.15·recency(updated_at
      rank)``. Cosine dominates here BY DESIGN: the stable slots already
      reward salience, and the whole point of the relevant slots is that a
      years-old low-salience fact ("your dog is Maple") must beat fresher
      heavyweight memories when the user asks about exactly it — with an
      event-recall-style 0.5/0.3/0.2 split, decay + recency crush it (caught
      by the ``dog-fact-by-relevance`` memory probe).

    Pure (no I/O), same contract as ``rank_and_chain``: ``query_vec`` /
    ``mem_vecs`` are optional precomputed embeddings from ONE batched call
    (pass both or neither — never mix embedding spaces). ``mem_vecs`` is
    aligned index-for-index with ``memories`` as passed. Absent, the
    zero-config hash embedder runs in place. Output preserves "stable first,
    then relevant" order; ``memories_to_message`` renders it as-is.
    """
    indexed = [
        (i, m) for i, m in enumerate(memories) if str(getattr(m, "content", "")).strip()
    ]
    if len(indexed) <= k_stable + k_relevant:
        return [m for _, m in indexed]
    stable = indexed[:k_stable]
    rest = indexed[k_stable:]
    q = query_vec if query_vec is not None else embed(query)

    # Recency by updated_at rank (same normalized-rank shape as event recall).
    def _upd(m: object) -> datetime | None:
        v = getattr(m, "updated_at", None)
        return v if isinstance(v, datetime) else None

    by_age = sorted(rest, key=lambda t: (_upd(t[1]) is not None, _upd(t[1]) or datetime.min))
    rank = {i: (pos + 1) / len(by_age) for pos, (i, _) in enumerate(by_age)}

    def _vec(i: int, m: object) -> list[float]:
        if mem_vecs is not None:
            return mem_vecs[i]
        return embed(str(getattr(m, "content", "")))

    scored = [
        (
            0.65 * cosine(q, _vec(i, m))
            + 0.2 * effective_memory_salience(m, now or datetime.now(UTC))
            + 0.15 * rank[i],
            i,
            m,
        )
        for i, m in rest
    ]
    scored.sort(key=lambda t: (t[0], -t[1]), reverse=True)
    return [m for _, m in stable] + [m for _, _, m in scored[:k_relevant]]


def memories_to_message(
    memories: Sequence[object], max_n: int = 6, *, now: datetime | None = None
) -> dict[str, str] | None:
    """Render the top atomic memories as ONE factual system line (Phase 2b).

    Atomic memories are the distilled long-term layer (LLM-extracted facts +
    episode summaries from consolidation). Injecting them keeps months-old
    knowledge reachable even after the raw event chains have decayed out of
    recall. The caller passes either raw ``list_memories`` output (active-only,
    salience-then-recency ordered, scope-filtered) or a ``rank_memories``
    selection; this takes the top ``max_n`` and renders them compactly.

    P0 (temporal grounding): a memory with ``created_at`` renders with its
    relative age — "(3 months ago)" — so the model can tell a years-old fact
    from last week's. Rows without timestamps render exactly as before.

    Same honesty contract as ``chains_to_messages``: facts only, no performed
    empathy — the persona block carries the voice.
    """
    rows = [m for m in memories if str(getattr(m, "content", "")).strip()][:max_n]
    if not rows:
        return None
    now = now or datetime.now(UTC)

    def _fact(m: object) -> str:
        content = str(getattr(m, "content", "")).strip()
        created = getattr(m, "created_at", None)
        if isinstance(created, datetime):
            return f"{content} ({relative_time(created, now)})"
        return content

    facts = " | ".join(_fact(m) for m in rows)
    return {
        "role": "system",
        "content": f"Facts you have learned about them (distilled from past conversations): {facts}",
    }


def open_loops_message(
    memories: Sequence[object],
    *,
    now: datetime | None = None,
    max_n: int = OPEN_LOOP_MAX,
    max_age_days: float = OPEN_LOOP_MAX_AGE_DAYS,
) -> dict[str, str] | None:
    """Render fresh unresolved threads (P1 open loops) as one system line.

    Loops are ``Memory`` rows whose tags include ``open_loop`` (minted and
    resolved by the extractor). Only the ``max_n`` most recently updated
    surface, and none older than ``max_age_days`` — a loop nobody touched for
    weeks is presumed resolved off-app; it stays in /memory but stops being
    brought up. The wording asks, never demands: proactive continuity must
    not become nagging. Lives in the memory package (not the router) so the
    eval gate can probe it litellm-/fastapi-free."""
    now = now or datetime.now(UTC)

    def _upd(m: object) -> datetime | None:
        v = getattr(m, "updated_at", None)
        return v if isinstance(v, datetime) else None

    loops = [
        m
        for m in memories
        if "open_loop" in (getattr(m, "tags", None) or [])
        and _upd(m) is not None
        and (now - _upd(m)).total_seconds() / 86_400.0 <= max_age_days  # type: ignore[operator]
    ]
    if not loops:
        return None
    loops.sort(key=lambda m: _upd(m), reverse=True)  # type: ignore[arg-type,return-value]
    parts = []
    for m in loops[:max_n]:
        content = str(getattr(m, "content", "")).strip()
        if not content:
            continue
        created = getattr(m, "created_at", None)
        when = f" ({relative_time(created, now)})" if isinstance(created, datetime) else ""
        parts.append(f"{content}{when}")
    if not parts:
        return None
    return {
        "role": "system",
        "content": (
            "Open threads you have not heard the outcome of (auto-tracked): "
            f"{' | '.join(parts)}. If — and only if — it fits the conversation naturally, "
            "you may ask how it went. Never force it and never ask about more than one."
        ),
    }


def _label(event, family_members: dict[str, str]) -> str:  # type: ignore[no-untyped-def]
    """Render an event as a short attributed phrase.

    Family-scope user events with a mapped ``participant_user_id`` are
    rendered as ``"{name}: {content}"`` (e.g. "Alex (parent): I have been
    stressed at work"). The map is keyed by user_id; the value is what the
    family owner set in the family settings (the persona block supplies
    the family-wide label so the prompt stays consistent).

    Unmapped / non-user rows fall back to the default they/you/note phrasing.
    """
    if (
        event.role == "user"
        and family_members
        and event.participant_user_id
        and event.participant_user_id in family_members
    ):
        return family_members[event.participant_user_id]
    return {
        "user": "they said",
        "assistant": "you said",
        "system": "note",
    }.get(event.role, event.role)


__all__ = [
    "MEMORY_K_RELEVANT",
    "MEMORY_K_STABLE",
    "OPEN_LOOP_MAX",
    "OPEN_LOOP_MAX_AGE_DAYS",
    "chains_to_messages",
    "effective_memory_salience",
    "effective_salience",
    "memories_to_message",
    "open_loops_message",
    "rank_and_chain",
    "rank_memories",
    "relative_time",
]
