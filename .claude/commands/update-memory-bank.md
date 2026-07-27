---
description: Review and update all memory-bank/ files to match the current project state
---

Update the project Memory Bank (`memory-bank/`).

Review **every** file, even if some need no changes. Focus on `activeContext.md` and `progress.md` — they track live state.

Process:

1. Read all six files: `projectbrief.md`, `productContext.md`, `systemPatterns.md`, `techContext.md`, `activeContext.md`, `progress.md`.
2. Compare against the current session: what was built/fixed/decided/discovered since the "Last updated" dates? Also check for drift against reality (e.g. run the verification commands' last known results, new files/routers/screens, changed invariants).
3. Update:
   - `activeContext.md` — current focus, next steps, recent decisions, verification baseline, "Last updated" date.
   - `progress.md` — move finished items into "What works", update "What's left", append to the decision log (newest first), known issues, "Last updated" date.
   - `systemPatterns.md` / `techContext.md` — only if architecture, file map, commands, or gotchas changed.
   - `projectbrief.md` / `productContext.md` — only on genuine scope/brand changes (rare; confirm with the user if unsure).
4. Keep entries **compressed**: the bank is a map, not a transcript. Summarize; link to code paths (`apps/api/.../file.py`) instead of pasting code. Delete stale "next steps" that are now done.
5. Never put secrets, keys, or `sk-` strings in the bank.
6. Report a short diff summary of what changed in the bank.

If the arguments contain a specific topic (`$ARGUMENTS`), prioritize capturing that topic, then still do the full review.
