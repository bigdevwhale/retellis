# System Patterns — Retellis

*Architecture and recurring patterns. For full detail see `CLAUDE.md` (auto-loaded); this file is the fast map of where things live and how they connect.*

## Monorepo map (polyglot pnpm)

```
apps/web/                     Next.js 15 PWA (App Router)
  app/                        routes; feature pages are server components wrapping GuestFeature
  components/                 Screens (ChatScreen, JournalScreen, …), TopBar, Rail, GuestShowcase, ui/
  lib/                        vault.ts (now a thin shim — sealKeyToServer ECDH seal only; no vault/IDB),
                              auth.tsx (AuthGate), i18n.tsx, api-client, toast.ts, public-routes.ts
  middleware.ts               allow-lists public routes
apps/api/src/ai_companion_api/
  main.py                     FastAPI app assembly (middleware, ratelimit, routers)
  routers/                    health, providers, memory, journal, routing, llm (SSE), auth, billing, family,
                              messengers (Telegram CRUD + bind)
  llm/provider.py             build_chain — the key-precedence chain (see below)
  routing/                    run_with_fallback, budget.compute_budget, router.py (dashboard)
  memory/                     store.py (Protocol + InMemory + Postgres), recall.py, context_builder.py,
                              persona_block.py, embeddings.py, salience.py, salience_llm.py
  turn/                       orchestrator.py — run_turn (non-streaming, for messengers; parallel to _stream)
  messengers/                 base.py (MessengerAdapter Protocol), store.py (InMemory + Postgres + make_),
                              registry.py (build_adapter_registry), connect_token.py (HMAC handshake token),
                              polling.py (MessengerPoller — one asyncio task per active bot), telegram/
                              (bot_api, types, adapter, commands). Future WhatsApp/Signal/Discord = new
                              adapter subdir + registry entry — core untouched.
  crypto/envelope.py          EnvelopeCipher (NaCl SecretBox) — server-side bot_token AND BYOK api_key
                              envelope (api_key_ciphertext / byok_enc_blob) under MESSENGER_TOKEN_DEK
  vault/                      session_ecdh.py (server keypair — one-time onboarding seal), zeroize.py (zeroized() ctx manager)
  auth/                       middleware (Principal), bootstrap.validate_auth_config, backends
  billing/                    providers.py (Paddle/ЮKassa/Prodamus), store
  observability/redaction.py  redact() — every client/log string passes through it (incl. TG bot-token RE)
  migrations/                 alembic (0016 = FK cascades, 0017 = billing customer_id, 0022 = messengers)
packages/contracts/           SINGLE SOURCE OF WIRE SHAPES: src/py pydantic ↔ src/ts zod,
                              schema.json generated, scripts/check_drift.mjs compares
packages/eval/                empathy eval gate (sys.path-injects api src; no fastapi install)
deploy/                       docker-compose.yml, Caddyfile (Caddy = single origin :80)
```

## The security-critical path: key precedence chain

`routers/llm.py` → `llm/provider.build_chain` → ordered `RoutingCandidate` list, always ending in mock:

**BYOK enc_key_blob → `LITELLM_API_KEY_<KIND>` env (openai → anthropic → openrouter → google) → Ollama (local keyless OR Cloud via `{base_url}/v1` + `openai/` prefix) → MockAdapter**

- BYOK wins over env; the env entry matching the BYOK kind is skipped.
- BYOK key decrypted in-memory (`DecryptedKey`), **zeroized after the whole chain runs**, even on exception (`zeroized()`).
- Budget checked first: ≥100% hard-stop → mock only; ≥80% soft-warn → proceed.
- `run_with_fallback` advances on 429/5xx/timeout/conn-refused, emitting SSE `fallback` events.

## Server-side envelope key storage (no client vault)

BYOK API keys live **server-side, envelope-encrypted** under `MESSENGER_TOKEN_DEK` (same `EnvelopeCipher` / NaCl `SecretBox` XSasha20-Poly1305 as the Telegram `bot_token`). No client vault, no IndexedDB, no passphrase, no master key (that scheme was removed 2026-07-23). `apps/web/lib/vault.ts` is reduced to `sealKeyToServer(plaintext, serverPubB64)` (the one-time onboarding ECDH `crypto_box_seal`) + the `getHealth` ecdh_pub fetch.

- **Onboarding (once):** client ECDH-seals the plaintext key to the server session pubkey (`/v1/health` → `ecdh_pub`) → `POST /v1/providers {key_handle, enc_key_blob}`. Server opens it with the session ECDH private key (`vault/decrypt.py`) → `envelope.encrypt_b64(DEK)` → `providers.api_key_ciphertext` (migration `0023_byok_envelope`; `family_providers.api_key_ciphertext` mirrors it; old `enc_blob`/`family_enc_blob_seed` columns kept as dead back-comat).
- **Per turn:** client sends `byok_enc_key_blob: null`; server reads `api_key_ciphertext`, envelope-decrypts in memory inside `with zeroized(...)`, builds a `DecryptedKey`, runs the LiteLLM call, zeroizes after. The legacy per-turn client ECDH blob path is kept as a back-comat fallback. Family `rotate passphrase` flow removed (no passphrase — rotation = delete + re-add).
- **Honest disclosure:** the server HOLDS the DEK and CAN decrypt BYOK keys at reply time — this is **NOT zero-knowledge**, disclosed plainly. It protects against a DB dump, not against the server operator. Plaintext lives only in request scope + zeroized (immutable heap strings excepted — the existing honest-zeroize disclosure stays). Keys survive a browser-data wipe + work across devices. `make_envelope` graceful-disables (returns None + logs) when hosted+no DEK → BYOK create 503s (NOT a boot crash, mirrors messenger behavior); self-hosted+no key → ephemeral + warn.

## Memory pipeline (per turn, `memory_on`)

recall_chains (rank + walk `prev_event_id` back → 2–4 intact chains) → `build_context([persona_block, salient_chains, recent_window, current_msg])` → stream → `append_event` (links user+assistant via `prev_event_id`, persists `usage`) → if salience ≥ 0.3, LLM `extract_memories` upserts atomic `Memory` rows (`active`/`superseded`). Judge+extract run **inside** the zeroized window (after `usage`+`done` are emitted); memory persistence runs after the stream, best-effort, logged on failure. Donor (shared) memories are read-only to receivers; `_apply_memory_ops` gates ops to own ids, stores re-filter by scope (defense-in-depth). Embedder = deterministic 384-dim feature hashing; salience = heuristic (honest limits; LLM versions wired but post-MVP).

## SSE stream contract (`POST /v1/llm/stream`)

`session → token(×N) → [fallback(×N)] → usage → done`; mid-stream `error` is redacted and `done` always follows. `done_sent` guard prevents double-done. Turn idempotency via client-minted `request_id`. Per-convo asyncio.Lock serializes `append_event`.

## Auth model

`AuthMiddleware` resolves a verified `Principal` (cookie → session → user) onto `request.state.principal`; missing identity → 401 (no default-user fallback). `X-User-Id` honored only with `AUTH_ALLOW_INSECURE_USER_HEADER=1`, and boot **hard-fails** if that is set in hosted mode (also hard-fails hosted + http origin). Everything re-filters by `user_id`/`persona_id`/`family_id` at store level too. Caller-supplied `family_id` checked against principal → 404 on mismatch. Cross-user access → **404, not 403**. User deletion cascades via FK `ON DELETE` (migration 0016). Rate limits: slowapi, per-IP on auth endpoints, per-user+per-IP on `/llm/stream` (disabled in test suite).

## Billing pattern

`_provider_for_country`: RU → ЮKassa else Prodamus; WW → Prodamus else Paddle. Paddle grants only on `transaction.completed`; `subscription.*` only updates status. Prodamus: HMAC `Sign` is the sole webhook auth, `order_num = retellis:<user_id>:<plan_slug>:<nonce>` (`:` because user ids are dashed UUIDs). Webhook routes are in `_PUBLIC_POST`.

## Messenger integration pattern (Telegram, 2026-07-23)

Per-user bot (each user pastes their own @BotFather token in Settings → Integrations). Long polling: one `MessengerPoller` asyncio task per active bot in the API lifespan (exponential backoff 1s→30s; `TokenInvalid`/`FatalError` → mark row `error` + stop; `EnvelopeDecryptError` → mark undecryptable + stop → user re-binds). 1:1 DM = personal account + one persona, shared event-chain memory with the web app (same `user_id`+`persona_id`).

- **`bot_token` storage = server-side envelope encryption** (NaCl SecretBox / XSalsa20-Poly1305 in `crypto/envelope.py`, NOT Fernet — cryptography lib isn't installed). Plaintext lives only inside the poller's per-turn scope; masked (`…last4`) is the only thing returned to the client. This is **NOT** zero-knowledge — honestly disclosed in UI. `make_envelope`: hosted+no `MESSENGER_TOKEN_DEK` → graceful disable (returns None + logs; the optional feature must not crash the API); self-hosted+no key → ephemeral + warn (restart = re-bind). **As of 2026-07-23 BYOK API keys share this same envelope scheme** (`providers.api_key_ciphertext` / `family_providers.api_key_ciphertext` under the same `MESSENGER_TOKEN_DEK`) — see "Server-side envelope key storage" above.
- **BYOK through the bind** (post-2026-07-23): the bind no longer carries a BYOK blob — the server already has the key in `api_key_ciphertext`. `POST /v1/messengers/telegram/{id}/bind` just approves the persona; the poller reads `api_key_ciphertext`, envelope-decrypts inside `with zeroized(...)`, and runs `build_chain` directly. The legacy `byok_enc_blob` / `_reseal_byok` path is kept as back-comat but new clients send null. The existing `_stream` is **untouched** — `turn/orchestrator.py::run_turn` is a parallel non-streaming path (no SSE, typing action + full reply).
- **Connect-token** = HMAC-SHA256 via `auth.sessions.seal/open_sealed` (secret = `auth_state_secret`), TTL `messenger_connect_token_ttl_seconds` (600s). `/start <token>` in Telegram → deep-link `https://<origin>/connect/telegram?messenger=&token=` → web Approve.
- Redaction: `_TG_BOT_TOKEN_RE` (`<8-12 digits>:<30+ base64-url>`) keeps the bot_id visible, erases the secret half. `enc_blob`/`bot_token_ciphertext` are colon-less base64 so they never match.
- `grep -r 'sk-' deploy/` still empty — Telegram tokens are `<digits>:<base64>`, not `sk-`.

## Security invariants (never regress — test-guarded)

1. `grep -r 'sk-' deploy/` returns nothing; `redact()` scrubs sk-/AIza/Bearer/prodamus_/TG-bot-token from all logs + Langfuse metadata. `api_key_ciphertext` (and the dead `enc_blob` column) are colon-less base64 — no `sk-` literal.
2. Keys never stored server-side in plaintext — only envelope ciphertext (`api_key_ciphertext` / `bot_token_ciphertext` / `byok_enc_blob`, NaCl `SecretBox` under `MESSENGER_TOKEN_DEK`). The server HOLDS the DEK and CAN decrypt both BYOK keys and bot tokens at reply time — **NOT zero-knowledge**, honestly disclosed. It protects against a DB dump, not against the server operator. Plaintext lives only in request scope + zeroized.
3. Client-facing errors redacted; never carry key material. Magic-link token + messenger connect-token never reach structured logs. Contract `Provider`/`FamilyProvider` never carry `api_key_ciphertext` — `key_handle` only returned to the client; `ProviderCreate`/`FamilyProviderCreate` (router-local) carry the one-time `enc_key_blob`.
4. Honest zeroize disclosure — never claim "erased from all memory" (source bytearray wiped; immutable str on managed heap cannot be wiped by us — applies to BYOK api_key AND bot_token).
5. Journal has no LLM call and no key surface.
6. Messenger `bot_token` plaintext lives only in the poller's per-turn scope; masked-only is ever returned to the client; `_TG_BOT_TOKEN_RE` scrubs the secret half from logs.

## Contract-change ritual

Any wire-shape change = edit **both** `packages/contracts/src/py/.../models.py` (+ REGISTRY in gen script) **and** `src/ts/index.ts`, then `pnpm contracts:check`. CI runs drift check `--strict`.
