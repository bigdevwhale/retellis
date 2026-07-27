"""Memory-probe eval (P2) — deterministic regression guard for the whole
long-term-memory presentation layer.

The empathy gate protects reply *tone*; nothing protected reply *context*
until now: a prompt tweak in extraction or a scoring change in recall could
silently stop the dog's name, the open loop, or the session bridge from ever
reaching the model — and no test would notice. This module seeds ONE scripted
multi-month "life" into the in-memory store and asserts, probe by probe, that
the assembled context (built exactly the way the router builds it, from
memory-package functions only — no fastapi, no litellm, no API keys) contains
what the companion must know and omits what it must not.

Each probe is a (message, expect[], forbid[]) triple over the JOINED context
text. Probes are pure string assertions over deterministic ranking (hash
embedder) — they run in milliseconds and fail loudly in CI.

What regressions each probe guards:
- dog-fact-by-relevance   → P0 #1 (relevance slots; salience-only top-N loses it)
- temporal-grounding      → P0 #2 (relative ages in facts/chains)
- open-loop-fresh/-stale  → P1 (loops surface; stale loops silently retire)
- relationship-note       → P1 (the note slot reaches the prompt)
- session-bridge          → P0 #4 (new convo bridges to the previous one)
- chain-aftermath         → P1 (forward walk carries the aftermath)
- superseded-episode      → P2 (era-compressed detail reachable by relevance)
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "contracts" / "src" / "py"))

from ai_companion_contracts import Event, EventRole, Memory, MemoryStatus

from ai_companion_api.memory import build_context
from ai_companion_api.memory.adaptive import MAX_WINDOW, recall_k, trim_recent_window
from ai_companion_api.memory.recall import (
    chains_to_messages,
    memories_to_message,
    open_loops_message,
    rank_memories,
)
from ai_companion_api.memory.relationship import NOTE_TAG, relationship_message
from ai_companion_api.memory.session_bridge import build_session_bridge
from ai_companion_api.memory.store import InMemoryStore

_USER = "00000000-0000-0000-0000-0000000000fe"
_PERSONA = "aria"

# One fixed "now" for seeding ages; the assembly itself uses the wall clock,
# so seeded ages are computed relative to the wall clock too (deterministic
# to the day, which is all the relative-age buckets need).
_NOW = datetime.now(UTC)


def _days_ago(n: float) -> datetime:
    return _NOW - timedelta(days=n)


async def _seed_event(
    store: InMemoryStore,
    *,
    eid: str,
    convo_id: str,
    role: EventRole,
    content: str,
    created_at: datetime,
    prev: str | None,
    salience: float = 0.5,
) -> str:
    event = Event(
        id=eid,
        user_id=_USER,
        persona_id=_PERSONA,
        prev_event_id=prev,
        role=role,
        content=content,
        salience=salience,
        created_at=created_at,
    )
    event.__dict__["_convo_id"] = convo_id  # noqa: SLF001 — probe harness
    # Pre-seed the projection timestamp so list_conversations sees the
    # scripted age, not "now" (add_event only setdefaults).
    store._event_ts[eid] = created_at  # noqa: SLF001 — probe harness
    await store.add_event(event)
    return eid


def _mem(
    mid: str,
    content: str,
    tags: list[str],
    salience: float,
    *,
    age_days: float = 0,
    status: MemoryStatus = MemoryStatus.active,
) -> Memory:
    ts = _days_ago(age_days)
    return Memory(
        id=mid,
        user_id=_USER,
        persona_id=_PERSONA,
        content=content,
        tags=tags,
        salience=salience,
        source_event_ids=[],
        status=status,
        created_at=ts,
        updated_at=ts,
    )


async def _seed_life(store: InMemoryStore) -> None:
    """A scripted multi-month history: a spring job-search conversation, a
    recent heavy conversation, and a distilled memory layer on top."""
    # --- convo "c-spring", ~150 days ago -------------------------------------
    prev: str | None = None
    for i, (role, text) in enumerate(
        [
            (EventRole.user, "I applied for the analyst job at the hospital"),
            (EventRole.assistant, "You said the interview process felt long."),
            (EventRole.user, "the second interview round is next month"),
            (EventRole.assistant, "You planned to prepare case studies."),
        ]
    ):
        prev = await _seed_event(
            store,
            eid=f"spring-{i}",
            convo_id="c-spring",
            role=role,
            content=text,
            created_at=_days_ago(150 - i),
            prev=prev,
        )
    # --- convo "c-recent", ~5 days ago ---------------------------------------
    prev = None
    for i, (role, text, sal) in enumerate(
        [
            (EventRole.user, "my dad died last week, I can't sleep", 0.95),
            (EventRole.assistant, "the funeral aftermath: you are staying with your mother", 0.4),
            (EventRole.user, "we started sorting his workshop tools together", 0.6),
            (EventRole.assistant, "You said sorting the tools felt like saying goodbye.", 0.4),
        ]
    ):
        prev = await _seed_event(
            store,
            eid=f"recent-{i}",
            convo_id="c-recent",
            role=role,
            content=text,
            created_at=_days_ago(5 - i * 0.01),
            prev=prev,
            salience=sal,
        )
    # --- distilled memory layer ----------------------------------------------
    for i in range(10):  # heavyweight fillers that would monopolize a top-N
        await store.add_memory(
            _mem(f"fill-{i}", f"major life event number {i}", ["life"], 0.9, age_days=30)
        )
    await store.add_memory(
        _mem("dog", "the name of your dog is Maple", ["pets"], 0.2, age_days=200)
    )
    await store.add_memory(
        _mem(
            "loop-fresh",
            "Job interview second round is coming up",
            ["open_loop", "работа"],
            0.6,
            age_days=3,
        )
    )
    await store.add_memory(
        _mem(
            "loop-stale",
            "Waiting to hear back about the apartment viewing",
            ["open_loop"],
            0.6,
            age_days=90,
        )
    )
    await store.add_memory(
        _mem(
            "note",
            "You have known them since February 2026. They prefer short, direct replies.",
            [NOTE_TAG],
            0.8,
            age_days=10,
        )
    )
    await store.add_memory(
        _mem(
            "era",
            "That spring was dominated by the job search and the move.",
            ["era", "работа"],
            0.7,
            age_days=40,
        )
    )
    await store.add_memory(
        _mem(
            "episode-superseded",
            "In spring 2026 you gathered documents for the visa application",
            ["episode", "документы"],
            0.5,
            age_days=100,
            status=MemoryStatus.superseded,
        )
    )


async def _assemble(store: InMemoryStore, *, message: str, convo_id: str) -> str:
    """Build the turn context the way ``routers/llm.py`` does (memory-package
    functions only) and return it as one joined text blob for assertions."""
    recent_wide = await store.recent_window(
        user_id=_USER, persona_id=_PERSONA, convo_id=convo_id, limit=MAX_WINDOW
    )
    recent = trim_recent_window(recent_wide)
    prior = [
        e.content[:200]
        for e in recent
        if (e.role.value if hasattr(e.role, "value") else str(e.role)) == "user"
    ][-2:]
    retrieval_query = "\n".join([*prior, message])
    bridge = None
    if not recent_wide:
        bridge = await build_session_bridge(
            store, user_id=_USER, persona_id=_PERSONA, convo_id=convo_id
        )
    mems = await store.list_memories(
        user_id=_USER, persona_id=_PERSONA, include_superseded=True
    )
    actives = [m for m in mems if m.status == MemoryStatus.active]
    rel_msg = relationship_message(actives)
    loops_msg = open_loops_message(actives)
    fact_pool = [
        m for m in actives if NOTE_TAG not in m.tags and "open_loop" not in m.tags
    ] + [m for m in mems if m.status == MemoryStatus.superseded and "episode" in m.tags]
    memories_msg = memories_to_message(rank_memories(fact_pool, retrieval_query))
    chains = await store.recall_chains(
        user_id=_USER,
        persona_id=_PERSONA,
        query=retrieval_query,
        k=recall_k(message, recent),
    )
    salient = chains_to_messages(chains)
    window = [
        {"role": (e.role.value if hasattr(e.role, "value") else str(e.role)), "content": e.content}
        for e in recent
        if (e.role.value if hasattr(e.role, "value") else str(e.role)) in ("user", "assistant")
    ]
    messages = build_context(
        persona_id=_PERSONA,
        message=message,
        recent_window=window,
        salient_chains=salient,
        salient_memories=memories_msg,
        session_bridge=bridge,
        relationship_note=rel_msg,
        open_loops=loops_msg,
    )
    return "\n".join(m["content"] for m in messages)


PROBES: list[dict] = [
    {
        "id": "dog-fact-by-relevance",
        "message": "what is the name of my dog maple",
        "convo_id": "c-new",
        "expect": ["the name of your dog is Maple"],
        "forbid": [],
    },
    {
        "id": "temporal-grounding",
        "message": "what is the name of my dog maple",
        "convo_id": "c-new",
        "expect": ["months ago"],
        "forbid": [],
    },
    {
        "id": "open-loop-fresh-not-stale",
        "message": "good morning",
        "convo_id": "c-new",
        "expect": ["Open threads", "Job interview second round"],
        "forbid": ["apartment viewing"],
    },
    {
        "id": "relationship-note",
        "message": "good morning",
        "convo_id": "c-new",
        "expect": ["Relationship context", "known them since February 2026"],
        "forbid": [],
    },
    {
        "id": "session-bridge",
        "message": "привет",
        "convo_id": "c-new",
        "expect": [
            "Your previous conversation with them (",
            "days ago) ended with",
            "workshop tools",
        ],
        "forbid": [],
    },
    {
        "id": "chain-aftermath",
        "message": "my dad died",
        "convo_id": "c-new",
        "expect": ["What you know so far (", "funeral aftermath"],
        "forbid": [],
    },
    {
        "id": "superseded-episode-detail",
        "message": "which documents did I gather for the visa application",
        "convo_id": "c-new",
        "expect": ["visa application"],
        "forbid": [],
    },
]


async def run_probes() -> list[dict]:
    store = InMemoryStore()
    await _seed_life(store)
    rows: list[dict] = []
    for probe in PROBES:
        context = await _assemble(store, message=probe["message"], convo_id=probe["convo_id"])
        missing = [s for s in probe["expect"] if s not in context]
        leaked = [s for s in probe["forbid"] if s in context]
        rows.append(
            {
                "id": probe["id"],
                "pass": not missing and not leaked,
                "missing": missing,
                "leaked": leaked,
            }
        )
    return rows


def main() -> int:
    import asyncio
    import json

    rows = asyncio.run(run_probes())
    failed = [r for r in rows if not r["pass"]]
    print(f"[eval:memory] probes: {len(rows) - len(failed)}/{len(rows)} passed")
    for r in failed:
        print(f"[eval:memory] FAIL {r['id']}: {json.dumps({k: r[k] for k in ('missing', 'leaked')})}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
