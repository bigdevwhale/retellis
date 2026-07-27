"""Empathy eval gate — the single most important quality gate (PLAN §6).

For each task in ``tasks/lifeside_subset.jsonl`` we run two turns against the
mock adapter (deterministic, no provider cost):

- **memory-off**: context = ``[persona_block, current_msg]`` — no event store.
- **memory-on**: the task's prior turns are seeded into an in-memory event
  store, ``recall_chains(probe)`` returns ranked intact chains, and the context
  = ``[persona_block, salient_chains, current_msg]``.

The gate **passes iff**

    mean(empathy_on) >= mean(empathy_off)
    AND mean(recall_on) > mean(recall_off)

- empathy: ``judge_empathy.score_heuristic`` on the companion's reply (disclose,
  don't perform). With the mock, replies are identical on/off → equal means →
  the ``>=`` holds; the guard fires when a real model is swapped in and memory
  degrades the reply.
- recall: ``metric_recall.recall_hit`` — does any event in the recalled chains
  contain the expected answer? memory-on surfaces the seeded event via the real
  embed → rank → chain pipeline; memory-off has no chains → 0.

Stillside turns use the local mock (``mock.mock_reply``) so the package stays
self-contained. ``pnpm eval`` writes ``results/baseline.json`` and exits 0 on
pass, 1 on fail.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from statistics import mean

# Make the API memory layer + shared contracts importable without installing
# them. Only the light memory modules are imported (no fastapi/pynacl/litellm).
_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "contracts" / "src" / "py"))

from ai_companion_contracts import EventRole  # noqa: E402
from ai_companion_api.memory import (  # noqa: E402
    InMemoryStore,
    append_event,
    build_context,
    chains_to_messages,
)

from .judge_empathy import score_heuristic, score_with_llm
from .memory_probe import run_probes
from .metric_recall import recall_hit
from .mock import mock_reply

TASKS = Path(__file__).resolve().parents[2] / "tasks" / "lifeside_subset.jsonl"
BASELINE = Path(__file__).resolve().parents[2] / "results" / "baseline.json"

_USER_ID = "00000000-0000-0000-0000-000000000001"
_CONVO = "eval-convo"


def load_tasks() -> list[dict]:
    if not TASKS.exists():
        return []
    return [json.loads(line) for line in TASKS.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _seed_store(task: dict) -> InMemoryStore:
    store = InMemoryStore()
    persona_id = task["persona_id"]
    for turn in task["turns"]:
        role = EventRole(turn["role"])
        await append_event(
            store,
            user_id=_USER_ID,
            persona_id=persona_id,
            convo_id=_CONVO,
            role=role,
            content=turn["content"],
        )
    return store


async def run_task(task: dict, *, use_llm: bool) -> dict:
    persona_id = task["persona_id"]
    probe_q = task["probe"]["question"]
    expected = task["probe"]["expected_recall"]

    # --- memory-off ---
    ctx_off = build_context(persona_id=persona_id, message=probe_q)
    reply_off = mock_reply(ctx_off)
    empathy_off = score_heuristic(reply_off)
    recall_off = 0.0

    # --- memory-on ---
    store = await _seed_store(task)
    chains = await store.recall_chains(user_id=_USER_ID, persona_id=persona_id, query=probe_q, k=3)
    salient = chains_to_messages(chains)
    ctx_on = build_context(persona_id=persona_id, message=probe_q, salient_chains=salient)
    reply_on = mock_reply(ctx_on)
    if use_llm:
        empathy_on = await score_with_llm(reply_on)
        empathy_off = await score_with_llm(reply_off)
    else:
        empathy_on = score_heuristic(reply_on)
    recall_on = recall_hit(chains, expected)

    return {
        "id": task["id"],
        "empathy_off": empathy_off,
        "empathy_on": empathy_on,
        "recall_off": recall_off,
        "recall_on": recall_on,
    }


async def run_all(*, use_llm: bool) -> dict:
    tasks = load_tasks()
    rows = [await run_task(t, use_llm=use_llm) for t in tasks]
    empathy_off = mean(r["empathy_off"] for r in rows) if rows else 0.0
    empathy_on = mean(r["empathy_on"] for r in rows) if rows else 0.0
    recall_off = mean(r["recall_off"] for r in rows) if rows else 0.0
    recall_on = mean(r["recall_on"] for r in rows) if rows else 0.0
    passed = bool(rows) and empathy_on >= empathy_off and recall_on > recall_off
    return {
        "n_tasks": len(rows),
        "empathy_off": round(empathy_off, 4),
        "empathy_on": round(empathy_on, 4),
        "recall_off": round(recall_off, 4),
        "recall_on": round(recall_on, 4),
        "gate_pass": passed,
        "per_task": rows,
    }


def _write_baseline(result: dict) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    # Don't bloat the committed baseline with per-task rows.
    summary = {k: v for k, v in result.items() if k != "per_task"}
    summary["note"] = (
        "heuristic judge (zero-config) over the mock adapter; "
        "set ANTHROPIC_API_KEY to rerun with the Claude-Haiku-4.5 judge."
    )
    # newline="\n" so the file stays LF-only on Windows (the platform default
    # is CRLF, which trips biome's "endOfLine" rule against the repo's LF
    # editorconfig).
    BASELINE.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")


def _run_memory_probes() -> tuple[int, int]:
    """P2: the deterministic memory-probe suite (no LLM, no keys, ms-fast).
    Prints per-probe failures and returns (passed, total). Runs in BOTH modes
    — a broken context assembly must fail CI even under ``--check``."""
    rows = asyncio.run(run_probes())
    failed = [r for r in rows if not r["pass"]]
    print(f"[eval:memory] probes: {len(rows) - len(failed)}/{len(rows)} passed")
    for r in failed:
        detail = {k: r[k] for k in ("missing", "leaked")}
        print(f"[eval:memory] FAIL {r['id']}: {json.dumps(detail, ensure_ascii=False)}")
    return len(rows) - len(failed), len(rows)


def main() -> int:
    check = "--check" in sys.argv
    use_llm = bool(os.environ.get("ANTHROPIC_API_KEY")) and not check
    result = asyncio.run(run_all(use_llm=use_llm))
    probes_passed, probes_total = _run_memory_probes()
    probes_ok = probes_passed == probes_total
    result["memory_probes"] = {"passed": probes_passed, "total": probes_total}

    if check:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
        print(
            f"[eval] tasks loaded: {result['n_tasks']} | "
            f"baseline: empathy_on={baseline.get('empathy_on')} "
            f"recall_on={baseline.get('recall_on')} pass={baseline.get('gate_pass')}"
        )
        return 0 if probes_ok else 1

    print(
        f"[eval] n={result['n_tasks']} "
        f"empathy_off={result['empathy_off']} empathy_on={result['empathy_on']} "
        f"recall_off={result['recall_off']} recall_on={result['recall_on']}"
    )
    _write_baseline(result)
    if result["gate_pass"] and probes_ok:
        print("[eval] PASS — empathy not degraded, recall improved, memory probes green.")
        return 0
    if not probes_ok:
        print("[eval] FAIL — memory probes failed (context assembly regression).")
    else:
        print("[eval] FAIL — memory degraded empathy or did not improve recall.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())