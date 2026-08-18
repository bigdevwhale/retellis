# Stillside

> An AI companion that doesn't pretend to feel. Open-source, self-hostable, bring-your-own-key.

`still` — calm. `side` — by your side. Stillside is an AI psychotherapist / friend / coach you run on your own machine, with your own LLM keys, your own model choices, and your own budget. It remembers what matters emotionally, and it is honest about what it is.

The one line we won't cross: **disclose, don't perform.** The companion never claims feelings it doesn't have. The UI never claims confidentiality it can't guarantee. This is the brand and the differentiator — not a footnote.

![license](https://img.shields.io/badge/license-Apache--2.0-533afd) ![node](https://img.shields.io/badge/node-%E2%89%A520-1a2233) ![python](https://img.shields.io/badge/python-3.12-1a2233) ![pnpm](https://img.shields.io/badge/pnpm-10.33.2-1a2233) ![status](https://img.shields.io/badge/status-MVP-9b6829)

---

## Why Stillside?

**1. Event-chain memory with emotional salience — not plain RAG.**
Recall returns 2–4 *intact conversation chains* ranked by `0.5·cosine + 0.3·salience + 0.2·recency`, walked backward along `prev_event_id` — not isolated snippets stripped of context.

**2. BYOK with swappable providers. Your key, your model, your budget.**
Bring an OpenAI, Anthropic, OpenRouter, Google, Azure, AIHubMix, or local Ollama key. Swap providers per turn. A monthly budget with an 80% soft-warn and a 100% hard-stop keeps spend bounded.

**3. Radically honest about security.**
Keys are envelope-encrypted server-side (NaCl `SecretBox` under `MESSENGER_TOKEN_DEK`), ECDH-sealed (`crypto_box_seal`) once in transit, and the decrypted key lives only in request memory and is zeroized after the reply. This is **not** zero-knowledge — we say so plainly. It protects against a database dump, not against the server operator. We'd rather tell you the truth than sell you a lie.

---

## In the room

> Screenshots are placeholders — drop calm chat and routing-dashboard frames into `docs/` and update the paths.

| A calm chat | Routing & budget |
|---|---|
| ![chat](docs/screenshot-chat.png) | ![routing](docs/screenshot-routing.png) |

Dark-mode first, weight-300 display type, warm-tinted neutrals on chat surfaces, engineered precision on config/routing surfaces. The full visual and voice contract lives in [`DESIGN.md`](DESIGN.md) — *calm, not cold; confidence through restraint.*

---

## Quickstart

Three commands from the repo root. The stack boots in Docker — Caddy on `http://localhost` proxies `/v1`→api and `/`→web on one origin so the session cookie is first-party.

```bash
pnpm install
cp deploy/.env.example .env
docker compose -f deploy/docker-compose.yml --project-directory . up
```

Then open **`http://localhost`** (port 80) — not `:3000` or `:8000` directly.

**What you need for a first reply.** The stack boots with no keys, but the companion needs at least one provider to answer:

- a BYOK key sealed onboarding (the in-app flow ECDH-seals it to the server once), **or**
- a `LITELLM_API_KEY_<KIND>` env var (`OPENAI` / `ANTHROPIC` / `OPENROUTER` / `GOOGLE`), **or**
- a local Ollama node (`OLLAMA_BASE_URL`).

No database is required to boot: the API falls back to an in-memory store when Postgres is unreachable, so you can explore without standing up pgvector. Set `COMPANION_USE_DB=1` to use the Postgres store with vector recall.

Prefer running the pieces directly? From `apps/api` with the repo `.venv` active:

```bash
uvicorn ai_companion_api.main:app --reload --port 8000
alembic upgrade head          # apply migrations (best-effort in the container CMD)
pnpm --filter @ai-companion/web dev          # web on :3000, dev-only /v1 rewrite
```

---

## What makes it different

**Event-chain vs. snippet RAG.** Plain retrieval-augmented chat pulls isolated snippets, which fragments emotional context — the gap that motivates this project puts RAG-on-companions empathy regression in the ~22–44% range. Stillside recalls whole chains: the user's message, the reply, the reply before it, linked by `prev_event_id`, so the companion keeps the thread instead of resurfacing detached facts.

**Injected, not remembered, persona.** The persona block (`persona_block.build_persona_block`) is rebuilt from config and injected into context **every turn**. The companion's voice cannot drift as the chain grows — the model never has to *remember* who it is.

**An empathy eval gate that blocks PRs.** `pnpm eval` runs a "disclose, don't perform" rubric over status-report tasks that probe persona and memory. A regression in empathy fails the gate (exit 1). Memory that breaks empathy doesn't ship.

**Honest Phase-3 limits.** The embedder is a deterministic 384-dim signed feature-hashing function and salience is a heuristic — zero-config, no API call, by design. Swapping in `litellm.embedding` + an LLM-judge salience (already wired, signatures stable) is a post-MVP upgrade, not a missing piece. We tell you this here so you don't find out later.

---

## Stack

| Layer | What |
|---|---|
| **Web** | Next.js PWA, React 19, TypeScript (strict), Tailwind, Serwist service worker, SSE streaming client |
| **API** | FastAPI, Python 3.12, LiteLLM, SQLAlchemy + pgvector, Langfuse observability, libsodium (NaCl) |
| **Contracts** | `packages/contracts` — pydantic models generate `schema.json`; zod schemas are compared to it. One source of truth, both languages. |
| **Memory store** | `InMemoryStore` (zero-config, graceful fallback) or `PostgresStore` (async SQLAlchemy + pgvector, exact cosine `<=>` until >50k events) |
| **Eval** | `packages/eval` — empathy gate, imports the memory layer without installing the full API |
| **Lint/format** | Biome (TS/JS, single quotes, 100 cols), Ruff (Python, 100 cols, py312) |

Toolchain: Node ≥20 (`.nvmrc`), pnpm@10.33.2 (`packageManager`), Python ≥3.12 (`.python-version`).

---

## Security, told straight

The security model is a feature, not an apology. The canonical, honest description of how your keys are stored — including what the server *can* decrypt — lives in [`SECURITY.md`](SECURITY.md); the load-bearing points:

- **Keys are never stored in plaintext.** BYOK API keys and the Telegram `bot_token` are stored as envelope ciphertext (`api_key_ciphertext`, NaCl `SecretBox` under `MESSENGER_TOKEN_DEK`). The contract `Provider`/`FamilyProvider` models never carry the ciphertext — only a `key_handle` is returned to the client.
- **Sealed once in transit.** Onboarding ECDH-seals the plaintext key to the server's session public key (`/v1/health` → `ecdh_pub`, `crypto_box_seal`). The server opens it with its session ECDH private key, envelope-encrypts, stores the ciphertext, drops the plaintext.
- **Zeroized per reply.** The decrypted key is resolved in memory inside `vault/zeroize.py`'s `zeroized()` context manager and the source `bytearray` is wiped after the call — on success *and* on exception.
- **`grep -r 'sk-' deploy/` returns nothing.** `observability/redaction.py` scrubs `sk-...` patterns from all logs and Langfuse metadata, gated by `tests/test_redaction.py`. Every string surfaced to the client or logs passes through `redact()`.
- **Per-provider model selection.** `Provider.model` is sent per request, so a BYOK candidate uses the user's chosen model; env-fallback and Ollama nodes keep server defaults.

**Honest zeroize disclosure (don't overstate):** once the plaintext key is read into an immutable Python `str` / JS `string` for the single LiteLLM call, those characters live on the managed heap and cannot be wiped by us. The source buffer is wiped; the key is never persisted, logged, or traced. We do not claim "erased from all memory."

**Honest scope disclosure:** the server *holds* `MESSENGER_TOKEN_DEK` and *can* decrypt BYOK keys at reply time. This is **not zero-knowledge**. It protects against a database dump, not against the server operator. Because keys live on the server, they survive a browser-data wipe and work across devices. If you want full key custody, self-host and hold the DEK yourself.

---

## Features

| | |
|---|---|
| **Memory** | Event-chain browser: linked threads, atomic memories (`active`/`superseded`), salience chips, donor memories from shares (read-only to receivers). |
| **Journal** | First-class CRUD + search surface — its own `journal_entries` table, ILIKE search + JSONB tag `@>` containment, scoped by `user_id`. **No LLM call, no key surface.** Mood/tags shown exactly as you authored them, never generated. |
| **Routing & budget** | 4-panel dashboard: fallback chain, budget ring, per-provider usage (requests / cost / tokens), last fallback. Provider health is configuration-derived, not live-probed. 80% soft-warn, 100% hard-stop. |
| **Personas** | 5 built-in personas + a full create form. Persona block injected every turn — voice is deterministic, not learned. |
| **Family** | Shared memory layer with private-by-default visibility; donor memories read-only to receivers; owner may share their active personal key with the family. Carries "not a licensed clinician · 112/911". |
| **Telegram** | Per-user bot, long polling, parallel orchestrator. `bot_token` envelope-encrypted with the same scheme as BYOK keys; BYOK re-seal via a deep-link handshake. |
| **Voice / PWA** | Installable, offline-friendly (Serwist), dark-mode first, `aria-live` streaming, reduced-motion honored. |

### Key precedence & fallback chain

Every turn calls `llm/provider.build_chain`, producing an ordered candidate list:

```
BYOK (api_key_ciphertext envelope) → LITELLM_API_KEY_<KIND> env → Ollama (local node or Ollama Cloud)
```

BYOK wins even when env keys exist; the env entry matching the BYOK kind is skipped (no self-fallback). On 429 / 5xx / timeout / connection-refused, `routing/run_with_fallback` emits an SSE `fallback` event and advances to the next candidate. The budget is checked first — a hard-stop skips real providers. Ollama runs as a keyless local node, or as Ollama Cloud via its OpenAI-compatible `{base_url}/v1` endpoint with the `openai/` prefix.

---

## Repo layout

```
ai-companion/
├─ apps/
│  ├─ api/                      FastAPI backend (Python 3.12)
│  │  ├─ src/ai_companion_api/
│  │  │  ├─ auth/               sessions, OIDC, magic-link, local, trusted-header
│  │  │  ├─ billing/            Paddle / ЮKassa / Prodamus
│  │  │  ├─ crypto/             envelope cipher (NaCl SecretBox)
│  │  │  ├─ db/                 SQLAlchemy models, alembic base
│  │  │  ├─ family/             shared memory scope, members, invites
│  │  │  ├─ llm/                provider build_chain, adapters
│  │  │  ├─ memory/             store, recall, persona_block, salience, embeddings, event_chain
│  │  │  ├─ messengers/telegram/ per-user bot, long polling
│  │  │  ├─ observability/      redaction, Langfuse
│  │  │  ├─ routers/            health, providers, memory, journal, routing, llm
│  │  │  ├─ routing/            fallback chain, budget
│  │  │  ├─ safety/
│  │  │  ├─ turn/               run_turn orchestration
│  │  │  └─ vault/              session ECDH, envelope decrypt, zeroize
│  │  ├─ migrations/versions/   alembic (0016 FK cascades, 0022 messengers, 0023 byok_envelope, …)
│  │  └─ tests/
│  └─ web/                      Next.js PWA (React 19, TS strict)
│     ├─ app/                   chat, memory, journal, practices, routing, persona, family, plans, onboarding, login, settings, connect
│     ├─ components/            AppShell, TopBar, Rail, screens, byok, vault, integrations, ui
│     ├─ lib/                   store, auth, i18n, theme, vault, providerCatalog, llm-client
│     └─ tests/
├─ packages/
│  ├─ contracts/                pydantic (src/py) + zod (src/ts) + drift check (scripts/)
│  └─ eval/                     empathy eval gate (gate.py, judge_empathy.py, memory_probe.py)
├─ deploy/                      docker-compose.yml, Dockerfile.{api,web}, Caddyfile, postgres/init.sql
├─ docs/                        prototype + screenshots
├─ memory-bank/                 Cline-style working memory (activeContext, progress, systemPatterns, techContext, projectbrief, productContext)
├─ DESIGN.md                    brand contract (load-bearing)
├─ CLAUDE.md                    operational guide + security invariants
└─ NOTICE                       third-party licenses
```

---

## Roadmap & status

**Honest scope.** Stillside is an MVP. The original plan fixed single-user as the MVP scope and pushed multi-user to post-MVP; the operator explicitly overrode that, so multi-user is in scope and largely wired (cookie sessions, magic links, families with a shared memory layer, everything scoped by `user_id` / `family_id`). **Clinical validation is out of scope** — Stillside is a companion, not a licensed clinician, and the UI says so. Self-hosted and hosted are both first-class `DEPLOYMENT_MODE`s; hosted enforces stricter boot validation (https origin, no insecure user-header escape hatch).

**Post-MVP, explicitly deferred:**
- `litellm.embedding` + an LLM-judge salience (`salience_llm.judge_salience`, already wired) replacing the deterministic embedder and heuristic salience — signatures stable, swap-in upgrade.
- Composer attachments; admin tooling for hosted mode.
- HNSW ANN beyond exact cosine for >50k events per scope.

Live status — what's done, what's left, known issues, and the decision log — is tracked in [`memory-bank/progress.md`](memory-bank/progress.md) and [`memory-bank/activeContext.md`](memory-bank/activeContext.md). The audit roadmap (K1–K8 / I1–I35 / M1–M5 findings, 8-sprint order) lives in the project plan referenced from [`memory-bank/projectbrief.md`](memory-bank/projectbrief.md).

---

## Contributing

Two gates must pass before a PR lands. Both are enforced in CI (`.github/workflows/ci.yml`):

```bash
pnpm eval                # empathy eval gate — exit 0 pass / 1 fail
pnpm contracts:check     # regen schema.json + zod↔pydantic drift check
```

Day-to-day:

```bash
pnpm typecheck           # tsc --noEmit across TS workspaces
pnpm lint                # biome check . (TS/JS)
pnpm format              # biome format --write .
pnpm test                # web vitest + api pytest + others
```

Single test:

```bash
pnpm --filter @ai-companion/web exec vitest run tests/byok-envelope.test.ts
pytest tests/test_memory_recall.py          # from apps/api, .venv active
```

Python lint is Ruff (line-length 100, py312, rules `E F I UP B`, `E501` ignored) — config in each package's `pyproject.toml`. Editor config: 2-space indent except Python (4-space), LF, final newline, trim trailing whitespace.

When you change a wire shape, change **both** the pydantic models and the zod schemas, then run `pnpm contracts:check`. After significant work, update `memory-bank/activeContext.md` and `memory-bank/progress.md` so the bank doesn't lag the code.

---

## License

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). Stillside depends on Next.js, React, FastAPI, LiteLLM, SQLAlchemy, pgvector, Langfuse, libsodium, Tailwind, Serwist, and Biome — their own licenses are retained at install time.

---

If a calm, honest companion you can fully self-host sounds like something you'd want — ⭐ it.

<!-- generated README v2 -->