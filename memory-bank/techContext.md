# Tech Context — Retellis

## Stack & versions

- **Node ≥20** (`.nvmrc`), **pnpm@10.33.2** (`packageManager`), **Python ≥3.12** (`.python-version`), repo-root `.venv` for Python packages.
- Web: Next.js 15 (App Router, async `cookies()`), TypeScript strict (`noUncheckedIndexedAccess`, `verbatimModuleSyntax`), vitest, Biome.
- API: FastAPI, SQLAlchemy async + pgvector, alembic, LiteLLM, sse-starlette, pynacl, slowapi, pytest (`asyncio_mode=auto`), Ruff.
- Contracts: pydantic ↔ zod, drift-checked (`scripts/check_drift.mjs --strict` in CI).
- Messenger envelope crypto: **pynacl SecretBox** (XSalsa20-Poly1305) in `crypto/envelope.py` — NOT Fernet; the `cryptography` lib is not installed, pynacl is. base64(nonce(24)‖ct) wire form.
- Windows 11 dev machine; PowerShell primary shell; paths contain spaces (`My Files`) — always quote.

## Messenger env vars (Telegram integration, 2026-07-23)

- `MESSENGER_TOKEN_DEK` — base64 32-byte NaCl SecretBox key for envelope-encrypting `bot_token` + `byok_enc_blob` **and (since 2026-07-23) BYOK `providers.api_key_ciphertext` / `family_providers.api_key_ciphertext`**. **Hosted + empty → feature gracefully disabled** (`make_envelope` returns None + logs; the API still boots, messengers just can't run and BYOK create 503s — re-add the DEK to revive). **Self-hosted + empty → ephemeral key generated in memory** with a warning (API restart invalidates all stored bot tokens + BYOK keys → bots go `error` / users re-add keys). Set it in `deploy/docker-compose.yml` / `.env.example` for persistence across restarts.
- `MESSENGER_LONG_POLL_ENABLED` — `true` (default). Toggle the poller loop entirely (set false to pause all bots without deleting them).
- `MESSENGER_POLL_TIMEOUT` — `30` (seconds). Telegram getUpdates long-poll window.
- `MESSENGER_CONNECT_TOKEN_TTL_SECONDS` — `600` (10 min). Lifetime of the `/start`→bind handshake token.

The messenger stack boots via `make_messenger_store(settings)` + `make_envelope(settings)` in `main.py` lifespan with `table_exists` fallback probes (like the other stores) so the API never fails to boot when PG is unreachable — it falls back to `InMemoryMessengerStore`.

## Commands (from repo root unless noted)

```bash
pnpm install                 # workspace install (+ pip-installs py pkgs into .venv)
pnpm dev                     # web dev server (:3000); API is NOT in pnpm dev
pnpm typecheck | lint | format | build | test
pnpm contracts:gen | contracts:check
pnpm eval | eval:check       # empathy gate, exit 0/1
pnpm docker:up | docker:down # full stack; browser entry = http://localhost (Caddy :80)
```

API dev server (no root script): from `apps/api` with `.venv` active:
`uvicorn ai_companion_api.main:app --reload --port 8000`; migrations `alembic upgrade head`.

Single tests:
```bash
pnpm --filter @ai-companion/web exec vitest run tests/byok-envelope.test.ts
pnpm --filter @ai-companion/web exec vitest run -t "name"
# from apps/api: pytest tests/test_memory_recall.py::test_name
```

## Gotchas (hard-won, do not rediscover)

- **docker compose needs `--project-directory .`** — relative paths in `deploy/docker-compose.yml` resolve from repo root; `--project-directory deploy` breaks mounts/builds.
- **Never point a browser at `:3000`/`:8000` in Docker** — production Next build has no `/v1` rewrite and the session cookie (SameSite=Lax) needs Caddy's single origin.
- **`libsodium-wrappers-sumo@0.7.16` has a broken ESM build** — `next.config.ts` and `vitest.config.ts` alias every import to the CJS entry via `createRequire().resolve()`; `lib/vault.ts` unwraps `mod.default`. **Never remove these aliases.**
- Contracts path alias `@ai-companion/contracts` → `packages/contracts/src/ts/index.ts` (tsconfig paths, NOT a built artifact — don't add a build step).
- Eval gate sys.path-injects `apps/api/src` + `packages/contracts/src/py` (deliberately no fastapi install); `gate.py` has ruff per-file ignores for `E402`/`I001`.
- Test suite disables rate limiting via `RATELIMIT_ENABLED=0` in conftest.
- `test_set_overwrites_audit_fields` has a pre-existing microsecond-timestamp flake (passes in isolation).
- Postgres store activates with `COMPANION_USE_DB=1`; otherwise InMemory (API never fails to boot).

## Style

- Biome: single quotes, semicolons, trailing commas, 100 cols, LF; `noNonNullAssertion`/`noArrayIndexKey` off.
- Ruff: line-length 100, py312, rules `E F I UP B` (`E501` ignored); config per-package in `pyproject.toml`.
- 2-space indent everywhere except Python (4-space). UTF-8, final newline.

## Verification checklist for a "done" change

`pnpm typecheck` + `pnpm lint` + web vitest + api pytest + `ruff check` + (if contracts touched) `pnpm contracts:check` + `pnpm eval` + `grep -r 'sk-' deploy/` empty. For messenger work also confirm `bot_token` never appears in client-facing output (only `bot_token_masked`) and that `_TG_BOT_TOKEN_RE` scrubs the secret half in `redact()`.
