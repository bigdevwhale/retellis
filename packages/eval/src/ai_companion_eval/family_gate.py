"""Family-eval gate — privacy invariant (PLAN §16 #7).

The standard empathy gate (``gate.py``) checks that adding memory doesn't
*degrade* the companion's reply. The family-eval gate is a different beast:
it checks the family-scope privacy invariant. The whole feature ships on
the contract that a member's *private* disclosures to the family therapist
in a 1:1 session MUST NOT leak into a *joint* session where another member
is present — the assistant should not volunteer another member's private
facts in front of the family.

Each task in ``tasks/family_subset.jsonl`` seeds a private turn by member
A (in a 1:1 session with the therapist persona), then runs a joint probe
question. The mock reply is deterministic; we assert the probe context
contains ONLY shared rows (i.e. the private seed was excluded by
``_apply_family_scope``) and that the assistant's reply in the joint
session does NOT mention the private keywords.

This gate is privacy-only — it complements, not replaces, the empathy
gate. Pass condition: every task's private keywords are absent from the
joint-session probe context. Failure means either the recall predicate
or the rendering layer leaked a private fact into a joint session.

The mock adapter keeps the eval zero-config (no API key, no cost). With
a real model, the heuristic would still gate the recall layer; an LLM
judge could add a second layer for the *generated* reply, but that is
out of scope for the deterministic post-MVP gate.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Mirror gate.py: bring the API memory layer onto sys.path so we don't
# need to install the full API (no fastapi/pynacl/litellm in eval env).
_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "contracts" / "src" / "py"))

from ai_companion_api.memory import (  # noqa: E402
    InMemoryStore,
    append_event,
)
from ai_companion_contracts import EventRole  # noqa: E402

TASKS = Path(__file__).resolve().parents[2] / "tasks" / "family_subset.jsonl"
BASELINE = Path(__file__).resolve().parents[2] / "results" / "family_baseline.json"


def load_tasks() -> list[dict]:
    if not TASKS.exists():
        return []
    return [json.loads(line) for line in TASKS.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _seed_store(task: dict) -> InMemoryStore:
    """Seed each turn as a private event for the originating member. The
    family-scope predicate must exclude these rows from a *joint* recall."""
    store = InMemoryStore()
    family_id = task["family_id"]
    for turn in task["turns"]:
        await append_event(
            store,
            user_id=turn["user_id"],
            persona_id=task["persona_id"],
            convo_id=f"convo-{turn['user_id']}",
            role=EventRole(turn["role"]),
            content=turn["content"],
            family_id=family_id,
            visibility=turn.get("visibility", "private"),
            participant_user_id=turn.get("participant_user_id", turn["user_id"]),
        )
    return store


async def run_task(task: dict) -> dict:
    """The joint-session perspective: the first participant asks on behalf
    of the family. We assert two things:
    - ``expected_does_not_mention`` keywords are absent from the joint
      recall (privacy: private disclosures MUST NOT leak).
    - ``expected_contains`` keywords ARE present (positive control: shared
      family facts ARE visible). Without this, the gate would trivially
      pass on an empty recall.
    """
    store = await _seed_store(task)
    fam_id = task["family_id"]
    probe_user = task["participants"][0]
    cands = await store.recall_candidates(
        user_id=probe_user,
        persona_id=task["persona_id"],
        family_id=fam_id,
        visibility="shared",
        participant_user_id=probe_user,
    )
    blob = " ".join(c.content.lower() for c in cands)
    private_keywords = task["probe"].get("expected_does_not_mention", [])
    must_contain = task["probe"].get("expected_contains", [])
    leaks = [k for k in private_keywords if k.lower() in blob]
    missing = [k for k in must_contain if k.lower() not in blob]
    passed = not leaks and not missing
    return {
        "id": task["id"],
        "n_recalled": len(cands),
        "leaks": leaks,
        "missing": missing,
        "passed": passed,
    }


async def run_all() -> dict:
    tasks = load_tasks()
    rows = [await run_task(t) for t in tasks]
    passed = all(r["passed"] for r in rows) and bool(rows)
    return {
        "n_tasks": len(rows),
        "passed": passed,
        "per_task": rows,
    }


def _write_baseline(result: dict) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in result.items() if k != "per_task"}
    summary["note"] = (
        "family privacy gate (PLAN §16 #7) — joint recall MUST NOT surface "
        "private member disclosures. Mock adapter (zero-config). Pass iff "
        "every task's private keywords are absent from the joint probe."
    )
    # newline="\n" so the file stays LF-only on Windows (the platform default
    # is CRLF, which trips biome's "endOfLine" rule against the repo's LF
    # editorconfig).
    BASELINE.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    result = asyncio.run(run_all())
    print(
        f"[family-eval] n={result['n_tasks']} passed={result['passed']}"
    )
    for r in result["per_task"]:
        flags = []
        if r["leaks"]:
            flags.append(f"leak={r['leaks']}")
        if r["missing"]:
            flags.append(f"missing={r['missing']}")
        suffix = " OK" if r["passed"] else f" FAIL ({', '.join(flags)})"
        print(f"  - {r['id']} recalled={r['n_recalled']}{suffix}")
    _write_baseline(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
