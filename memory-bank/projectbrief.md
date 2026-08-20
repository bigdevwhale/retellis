# Project Brief — Retellis

*Foundation document. All other memory-bank files build on this. Changes here are rare and deliberate.*

## What we are building

**Retellis** — an open-source AI companion PWA with **BYOK** (bring-your-own, swappable LLM API keys). Users chat with a configurable persona; the system remembers what matters emotionally and never pretends to be something it isn't.

## Core differentiators (vs plain RAG chatbots)

1. **Event-chain memory with emotional salience** — recall returns 2–4 *intact conversation chains* ranked by `0.5·cosine + 0.3·salience + 0.2·recency`, not isolated snippets.
2. **Deterministic, injected-not-remembered persona block** — the companion's voice is rebuilt every turn from config, so it cannot drift as history grows.
3. **Server-side envelope key storage (honestly disclosed, not zero-knowledge)** — API keys are sealed in transit once (ECDH `crypto_box_seal`) and stored envelope-encrypted on the server (NaCl `SecretBox` under `MESSENGER_TOKEN_DEK`, the same scheme as the Telegram `bot_token`). The server holds the DEK and *can* decrypt keys at reply time — this is **not** zero-knowledge, disclosed plainly; it protects against a database dump, not the server operator. Keys survive a browser-data wipe and work across devices. (The earlier "zero-knowledge client vault" design was superseded 2026-07-23 — see `CLAUDE.md` § BYOK key storage.)
4. **Honest limits ("disclose, don't perform")** — the UI never claims confidentiality it can't guarantee; the companion never claims feelings it doesn't have; no fabricated data anywhere in the UI.

## Scope decisions (authoritative)

- **Multi-user is IN scope** — the user explicitly overrode the original "post-MVP" constraint ("да, и сделай multi user"). Auth = cookie sessions + magic links; families with shared memory layer; everything scoped by `user_id`/`family_id`.
- **Billing is IN scope** — hosted mode with plans/credits via Paddle (WW), ЮKassa and Prodamus (RU; Prodamus also WW — suits a RU самозанятый operator whom Paddle blocks).
- **Self-hosted and hosted** are both first-class `DEPLOYMENT_MODE`s; hosted enforces stricter boot validation (https origin, no insecure header escape hatch).

## Load-bearing reference docs

- `CLAUDE.md` (at `.claude/CLAUDE.md`) — operational guide: commands, invariants, architecture summary, vault scheme.
- `DESIGN.md` — brand contract (voice, honest-limits wording).
- Audit roadmap plan file: `C:\Users\user\.claude\plans\bubbly-herding-popcorn.md` (K1-K8 / I1-I35 / M1-M5 finding IDs + 8-sprint order).
