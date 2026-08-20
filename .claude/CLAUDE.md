# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Memory Bank (read this FIRST)

`memory-bank/` is the project's persistent working memory (Cline-style). **At the start of any non-trivial task, read the relevant memory-bank files BEFORE exploring the codebase** — they answer most "where is X / how does Y work / what's the current state" questions without re-reading source files:

| File | Answers | Read when |
|---|---|---|
| `memory-bank/activeContext.md` | current focus, next steps, live decisions | **always** (start of every task) |
| `memory-bank/progress.md` | what's done, what's left, known issues, decision log | planning / status questions |
| `memory-bank/systemPatterns.md` | file map, key chain, key storage, memory pipeline, auth, invariants | touching backend/architecture |
| `memory-bank/techContext.md` | commands, versions, gotchas, verification checklist | running/setting anything up |
| `memory-bank/projectbrief.md` | scope, differentiators, authoritative decisions | scope questions |
| `memory-bank/productContext.md` | UX, brand contract, screens, honest-limits | touching UI/copy |

Only fall back to reading source files when the memory bank doesn't answer the question or when you need exact current code (the bank can lag reality — trust code over bank on conflict, then fix the bank).

**Keeping it fresh:** after completing significant work (a feature, a sprint item, an architectural decision, a discovered gotcha), update `activeContext.md` and `progress.md` (and other files if patterns/setup changed) — including the "Last updated" date. The `/update-memory-bank` command does a full review of all six files.

## What this is

**Retellis** — an open-source AI companion PWA with BYOK (bring your own swappable LLM API keys). The differentiator vs plain RAG is event-chain memory with emotional salience + a deterministic, *injected-not-remembered* persona block (see DESIGN.md and § Event-chain memory & recall below). The codebase is a **polyglot pnpm monorepo**: a Next.js PWA, a FastAPI backend, a shared contracts package (zod ↔ pydantic with a drift check), and a Python empathy-eval gate.

Read `DESIGN.md` (brand contract) — load-bearing reference, not docs. Architecture, data schema, vault scheme, and security invariants live in this file below.

## Commands

All commands run from the **repo root** unless noted. `package.json` scripts fan out with `pnpm -r`.

```bash
pnpm install                      # install (workspace; also builds .venv Python pkgs via pip)
pnpm dev                          # run web + api dev servers in parallel (api has no dev script — see below)
pnpm --filter @ai-companion/web dev          # web only (Next.js :3000)
pnpm typecheck                    # tsc --noEmit across TS workspaces
pnpm lint                         # biome check . (TS lint; Python uses ruff)
pnpm format                       # biome format --write .

pnpm build                        # build all TS packages
pnpm test                         # run all test suites (web vitest + api pytest + others)

pnpm contracts:gen                # regenerate packages/contracts/schema.json from pydantic
pnpm contracts:check             # gen + zod↔pydantic drift check (fails CI on divergence)
pnpm eval                         # run the empathy eval gate (exits 0 pass / 1 fail)
pnpm eval:check                   # eval gate in --check mode

pnpm docker:up                    # docker compose -f deploy/docker-compose.yml --project-directory . up (full stack)
pnpm docker:down                  # tear down
```

The compose file's relative paths (`./deploy/...`, build context `.`) resolve from the
**repo root**, so `--project-directory .` is required — using `--project-directory deploy`
rebases them to `deploy/deploy/...` and breaks volume mounts (Caddyfile, postgres init)
plus any image rebuild. The stack's single browser entry point is **Caddy on
`http://localhost`** (port 80), which proxies `/v1`→api and `/`→web on one origin so the
SameSite=Lax session cookie is first-party. Don't point a browser at `:3000`/`:8000` directly
in Docker — the production Next build has no `/v1` rewrite (dev-only) and the API's cookie
won't be first-party cross-origin.

The **Python API has no root dev script** — it runs in Docker (`pnpm docker:up`, API on :8000) or directly with uvicorn from `apps/api` using the repo `.venv`:

```bash
# from apps/api, with .venv active:
uvicorn ai_companion_api.main:app --reload --port 8000
alembic upgrade head               # apply migrations (best-effort in the container CMD)
```

### Single test

```bash
# Web (vitest) — from root:
pnpm --filter @ai-companion/web exec vitest run tests/byok-envelope.test.ts
pnpm --filter @ai-companion/web exec vitest run -t "name of test"

# API (pytest, asyncio_mode=auto) — from apps/api with .venv active:
pytest tests/test_memory_recall.py
pytest tests/test_vault_decrypt.py::test_name
```

### Lint per language

- **TS/JS:** Biome (`pnpm lint`, `pnpm format`) — single quotes, semicolons, trailing commas, 100 cols, LF. `noNonNullAssertion` and `noArrayIndexKey` are off.
- **Python:** Ruff (line-length 100, py312, rules `E F I UP B`, `E501` ignored). Config lives in each package's `pyproject.toml`.

## Architecture (the big picture)

### Two-language monorepo, one shared contract

`packages/contracts` is the **single source of truth for wire shapes**. The pydantic models (`src/py/ai_companion_contracts/models.py`) generate `schema.json`; the zod schemas (`src/ts/index.ts`) are converted to JSON-Schema and compared property-key-by-property-key by `scripts/check_drift.mjs`. **Changing a contract means changing both sides and running `pnpm contracts:check`.** The API imports `ai_companion_contracts` (pip-installed in Docker from `packages/contracts`); the web imports `@ai-companion/contracts` (TS path alias → `packages/contracts/src/ts/index.ts`).

### Key precedence & fallback chain (the security-critical path)

Every turn in `apps/api/.../routers/llm.py` calls `llm/provider.build_chain`, which produces an ordered `RoutingCandidate` list always ending in `MockAdapter`:

**BYOK (`api_key_ciphertext` envelope, or the back-comat per-turn `enc_key_blob`) → `LITELLM_API_KEY_<KIND>` env (openai → anthropic → openrouter → google) → Ollama → mock**

- BYOK wins even when env keys exist; the env entry matching the BYOK kind is skipped (no self-fallback).
- The BYOK key is resolved in-memory onto the candidate's `DecryptedKey` and **zeroized after the whole chain runs** — even if BYOK failed and a later candidate served the turn. New clients send `byok_enc_key_blob: null` per turn and the server resolves the key from `providers.api_key_ciphertext` (envelope-decrypts it inside the `zeroized()` window); the legacy per-turn client ECDH blob path is kept as a back-comat fallback. `vault/zeroize.py`'s `zeroized()` context manager wipes the bytearray on exception too. Env keys are process-lifetime `str` and not zeroized.
- `routing/run_with_fallback` walks the chain; on 429/5xx/timeout/connection-refused it emits an SSE `fallback` event and advances. The monthly budget (`routing/budget.compute_budget`) is checked first: **hard-stop ≥100%** skips real providers and serves mock (`reason: "budget hard-stop"`); **soft-warn ≥80%** proceeds normally.
- Ollama has two faces: a keyless local node (`OLLAMA_BASE_URL`), or Ollama Cloud which is routed through its OpenAI-compatible `{base_url}/v1` endpoint with the `openai/` model prefix (LiteLLM's native `ollama/` provider doesn't reliably send the Bearer key to the hosted endpoint). See `build_chain` for the local-vs-cloud branch.
- Per-provider **model selection**: `Provider.model` is sent per request so the BYOK candidate uses the user's chosen model; env-fallback and Ollama nodes keep server defaults (`DEFAULT_MODELS` in `llm/provider.py`).

### BYOK key storage (server-side envelope) and the one-time ECDH seal

BYOK API keys are stored **server-side, envelope-encrypted** under `MESSENGER_TOKEN_DEK` (the same `EnvelopeCipher` / NaCl `SecretBox` XSalsa20-Poly1305 scheme already used for the Telegram `bot_token`). There is **no client vault, no IndexedDB, no passphrase, no master key** — that scheme was removed 2026-07-23. `apps/web/lib/vault.ts` is reduced to a single `sealKeyToServer(plaintext, serverPubB64)` helper plus the `getHealth` ecdh_pub fetch; the legacy Argon2id/IndexedDB/`enc_blob`-sync code is gone.

**Onboarding (once):** the client ECDH-seals the plaintext key to the **server session public key** (`/v1/health` → `ecdh_pub`, libsodium `crypto_box_seal`) and sends it as `enc_key_blob` on `POST /v1/providers`. The server opens it with its session ECDH private key (`vault/decrypt.py`; server keypair generated once in `vault/session_ecdh.py` at startup), envelope-encrypts the plaintext (`crypto/envelope.py`, nonce‖ct base64) → stores `providers.api_key_ciphertext`. Family providers (`family_providers.api_key_ciphertext`) work identically. Migration `0023_byok_envelope` added the columns; the old `enc_blob` / `family_enc_blob_seed` columns remain as dead back-comat columns (unused).

**Per turn** (web `/v1/llm/stream`, Telegram `run_turn`): the client sends `byok_enc_key_blob: null`; the server reads `api_key_ciphertext`, envelope-decrypts it in memory inside the `zeroized()` window, builds a `DecryptedKey`, uses it for the LiteLLM call, and zeroizes after. The old per-turn client ECDH blob path is kept as a back-comat fallback (legacy clients that still send a blob), but new clients send null and the server is the source of truth for the active key.

**Honest disclosure (load-bearing — the "disclose, don't perform" brand contract):** the server HOLDS `MESSENGER_TOKEN_DEK`, so it **CAN** decrypt BYOK keys at reply time. This is **NOT zero-knowledge** — it protects against a database dump, not against the server operator. Plaintext keys live only in request memory and are zeroized after (immutable heap strings excepted — the existing honest-zeroize disclosure stays). Because keys live on the server, they survive a browser-data wipe and work across devices. The Telegram `bot_token` already used this envelope scheme; BYOK keys now share it. `make_envelope` keeps its graceful-disable policy (returns None + logs when hosted + no DEK; BYOK create 503s when the envelope is None, mirroring messenger behavior — NOT a boot crash).

`libsodium-wrappers-sumo@0.7.16` ships a **broken ESM build**. Both `apps/web/next.config.ts` and `apps/web/vitest.config.ts` alias every import to the CJS entry via `createRequire(...).resolve(...)`. `lib/vault.ts` also unwraps `mod.default` because a dynamic `import()` of a CJS module wraps `module.exports` in `.default`. **Do not remove these aliases** — the vault tests and the build will both break.

### Event-chain memory & recall (the empathy differentiator)

`apps/api/.../memory/` — the store has two implementations of one `MemoryStore` Protocol (`store.py`): `InMemoryStore` (tests, zero-config default, graceful fallback when DB unreachable) and `PostgresStore` (async SQLAlchemy + pgvector, used when `COMPANION_USE_DB=1`). `make_store` picks one and falls back so the API never fails to boot.

Per turn (`memory_on`): `recall.recall_chains` ranks events by `0.5·cosine + 0.3·salience + 0.2·recency`, then walks `prev_event_id` backward to return 2–4 **intact chains**. `context_builder.build_context` assembles `[persona_block, salient_chains, recent_window, current_msg]`. The **persona block is deterministic and injected every turn** (`persona_block.build_persona_block`) — so the companion's voice cannot drift as the chain grows. After the turn, `append_event` links user+assistant events via `prev_event_id`, persists a `usage` row, and (when the user message is salient enough, `EXTRACT_SALIENCE_THRESHOLD = 0.3`) runs an LLM `extract_memories` pass to upsert atomic `Memory` rows (`active`/`superseded` status). Donor memories from shares are read-only to the receiver; `_apply_memory_ops` validates update/drop ops against own-only memory ids.

**Honest limits (Phase 3):** the embedder is a deterministic 384-dim signed feature-hashing embedding (`embeddings.py`), and salience is a heuristic (`salience.py`) — both zero-config, no API call. Swapping in `litellm.embedding` + an LLM-judge salience (`salience_llm.judge_salience`, already wired) is a post-MVP upgrade that keeps the `embed`/`score_salience` signatures stable. Postgres recall uses exact cosine `<=>` until >50k events.

### Streaming endpoint contract

`POST /v1/llm/stream` is SSE (`sse-starlette`). Each `data:` line is a JSON object with a `type` field — the union is the `LlmStream*` zod/pydantic models in `@ai-companion/contracts`:

```
session → token(×N) → [fallback(×N)] → usage → done
(error mid-stream carries a redacted message; done always follows)
```

For BYOK, `usage` + `done` are emitted **before** the post-turn LLM work (judge + extract) so the user sees completion first while the key is still alive — the judge + extract calls run inside the `zeroized()` window. Memory persistence runs **after** the stream fully sends (key already zeroized for BYOK) and is best-effort: a memory failure never breaks a turn but is logged so silent "empty /memory" bugs are diagnosable.

### Routing dashboard & budget

`GET /v1/routing` (`routing/router.py`) returns `RoutingState`: the live chain (BYOK omitted — not tied to a turn), the budget rollup (spent/remaining/pct/warn/hard-stop), a per-provider usage summary rolled up from `usage` rows (requests/cost/tokens), the last fallback this process, and a Langfuse link-out. Provider health is **configuration-derived, not live-probed** (configured=healthy, Ollama-without-URL=standby, removed-provider-with-prior-usage=unavailable) — the dashboard does not ping providers. `fallback_last_turn` is process-local (lost on restart).

### Auth model (multi-user)

A verified `Principal` is resolved per request by `auth/middleware.AuthMiddleware` (cookie → `AuthStore.get_session` → `get_user` → `principal_from_user`) and attached to `request.state.principal`. `deps.get_current_user_id` / `get_current_principal` read it from there — they do **not** trust a self-asserted `X-User-Id` in production. The `X-User-Id` header is honored only behind the dev/test escape hatch `AUTH_ALLOW_INSECURE_USER_HEADER=1` (default off), and `auth/bootstrap.validate_auth_config` **hard-fails the boot** if that flag is set in `DEPLOYMENT_MODE=hosted` (it is a full impersonation surface). As of Sprint 6 M1.2 a missing header no longer silently impersonates `settings.default_user_id` (the config value survives only as a test fixture) — it 401s.

Everything is scoped by `user_id` (personal) or `family_id` resolved from the principal's membership (family). Every store method re-filters by `user_id`/`persona_id`/`family_id` — including the write paths (`update_memory`/`supersede_memory` take `user_id`+`persona_id`+`family_id` and no-op on a cross-scope id, defense-in-depth on top of `_apply_memory_ops`'s `existing_ids` gate). Caller-supplied `family_id` on `/v1/memory`, `/v1/conversations`, `/v1/memories`, `/v1/memory/recall`, and `/v1/journal` is checked against `principal.family_id` and 404s on mismatch (same contract as `/v1/llm/stream`). Endpoints that cross users return 404 (not 403) — see journal/memory deletes. User deletion cascades via FK `ON DELETE` (migration `0016`): a user's events/memories/journal/usage/providers/personas/sessions/shares/membership are cascade-deleted; a family they own is disbanded (CASCADE) and remaining members' `users.family_id` is SET NULL.

### Other endpoints

Routers in `apps/api/.../routers/` (all prefixed `/v1`): `health` (`/health` returns `ecdh_pub`), `providers` (BYOK metadata CRUD — key_handle only, never the key), `memory` (events, memories, recall, shares, reset), `journal` (first-class `journal_entries` table — **not** the chat event chain — ILIKE search + JSONB tag `@>` containment, scoped by `user_id`), `routing` (dashboard), `llm` (the stream). Journal is a pure CRUD + search surface: **no LLM call, no BYOK/key surface** — `grep 'sk-'` stays empty.

## Security invariants (do not regress)

These are guarded by tests and the README's security section — keep them intact when editing:

- **`grep -r 'sk-' deploy/` must return nothing.** `observability/redaction.py` scrubs `sk-...` patterns from all logs and Langfuse metadata, gated by `tests/test_redaction.py`. `api_key_ciphertext` (and the dead `enc_blob` column) are base64 ciphertext with no `sk-` literal. Every string surfaced to the client or logs passes through `redact()`.
- API keys are **never stored server-side in plaintext** — only envelope ciphertext (`api_key_ciphertext`, NaCl `SecretBox` under `MESSENGER_TOKEN_DEK`). The server **HOLDS the DEK and CAN decrypt BYOK keys at reply time** — this is **not zero-knowledge**, honestly disclosed (it protects against a database dump, not against the server operator). The decrypted key lives only in request scope and is zeroized after the call (even on exception). The contract `Provider`/`FamilyProvider` models never carry `api_key_ciphertext` — `key_handle` only is returned to the client; `ProviderCreate`/`FamilyProviderCreate` (router-local) carry the one-time `enc_key_blob`.
- Error messages to the client are **redacted and never carry key material**. `ProviderResolutionError` uses a redacted message.
- The UI **never claims confidentiality it can't guarantee** and the companion **never claims feelings it doesn't have** ("disclose, don't perform"). Don't add affective claims the system can't back. Journal surfaces `mood`/`tags` **as the user authored them**, never generated. BYOK keys are disclosed as server-managed envelope encryption (not zero-knowledge); the family `rotate passphrase` flow is gone (no passphrase to rotate — family key rotation = delete + re-add).

**Honest zeroize disclosure** (don't overstate): once the plaintext key is read into an immutable Python `str` / JS `string` for the single LiteLLM call, those characters live on the managed heap and cannot be wiped by us. The source `bytearray`/`Uint8Array` is wiped; the key is never persisted/logged/traced. Do not claim "erased from all memory."

## Conventions worth knowing

- **Editor config:** 2-space indent for everything except Python (4-space); LF line endings; final newline; trim trailing whitespace. UTF-8.
- **Node ≥20** (`.nvmrc`), **pnpm@10.33.2** (`packageManager`), **Python ≥3.12** (`.python-version`). `tsconfig.base.json` is strict: `noUncheckedIndexedAccess`, `noImplicitOverride`, `verbatimModuleSyntax`, `isolatedModules`.
- **Contracts path alias:** the web imports `@ai-companion/contracts` → `packages/contracts/src/ts/index.ts` (configured in `apps/web/tsconfig.json` paths, not a built artifact). Don't add a build step for contracts.
- The eval gate (`packages/eval`) imports the API memory layer by inserting `apps/api/src` + `packages/contracts/src/py` onto `sys.path` — it deliberately does **not** install the full API (no fastapi/pynacl/litellm). `gate.py` has per-file ruff ignores for the resulting `E402`/`I001`.
- **Reset paths are deliberately separate** (see README): `DELETE /v1/memory/convo` removes one thread's raw message events (derived memories persist); `DELETE /v1/memory?persona_id=` un-learns a persona entirely (events + all-status memories + outgoing shares). Both idempotent (204 on missing target). Neither touches key material.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).