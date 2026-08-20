# Product Context — Retellis

## Why this exists

People who want an AI companion today choose between closed products that harvest data and perform fake intimacy, or DIY RAG stacks that forget everything that matters. Retellis offers: your own keys, your own data (self-hostable), memory that follows emotional weight, and a companion that is honest about what it is.

## Brand contract ("disclose, don't perform")

- The companion **never claims feelings it doesn't have**; no affective claims the system can't back.
- The UI **never claims confidentiality it can't guarantee** (e.g. zeroize disclosure is honest: source buffers are wiped, but immutable strings on managed heaps cannot be). BYOK keys are **server-managed envelope encryption, not zero-knowledge** — the server holds `MESSENGER_TOKEN_DEK` and can decrypt them at reply time; this protects against a database dump, not against the server operator. **As of 2026-07-27 the full disclosure lives in `SECURITY.md`, not in the UI.** UI key copy is neutral-true ("encrypted in transit and at rest on the server") and must never imply zero-knowledge, on-device custody, or "only you can read it." The Settings → Key vault tab shows a one-line neutral statement + a link to `SECURITY.md`.
- **No fabricated data in the UI** — no fake key fingerprints (`sk-••••3a2f` from the OD design was deliberately NOT reproduced), no invented memory chips, no fake member lists. Journal shows `mood`/`tags` exactly as the user authored them, never generated.
- Practices screen carries "tools, not a replacement for therapy"; family screen carries "not a licensed clinician · 112/911".
- Your API keys are stored envelope-encrypted on the server; the server can decrypt them at reply time (not zero-knowledge). Clearing your browser data no longer loses your keys (they live on the server). The full disclosure is in `SECURITY.md`; the UI states only the neutral fact and links there. (Replaces the old "forgetting the vault passphrase = no recovery" line — there is no passphrase now.)

## The app surface (Next.js PWA, OD design system, Stripe palette)

- **Landing** (`/`) — 8-section marketing page (hero/diff/why/how/tech/pricing/limits/closing).
- **7-tab TopBar nav**: Chat / Memory / Journal / Practices / Routing / Personas / Family (+ Plans when billing on). Feature routes double as **guest showcase pages** for signed-out users (sample content taken verbatim from the OD design, honest badges), and the real screens for authed users. `/practices` is fully client-side and real for everyone.
- **Chat** — two-column (convo sidebar / mobile drawer), SSE streaming, fallback events, family mode, voice, retry, delete-undo.
- **Memory** — chains/memories browser. **Journal** — first-class CRUD + search (no LLM, no keys). **Practices** — breathing pacer + meditation timer, offline. **Routing** — 4-panel dashboard (fallback chain, budget ring, providers table, last fallback). **Personas** — 5 builtins + full create form. **Family** — members, therapy CTA, settings/invites.
- Onboarding seals the provider key to the server (ECDH, once) → server stores it envelope-encrypted; no passphrase, no client vault.

## Users

- **Self-hosters**: run the whole stack via docker compose, keyless local Ollama works out of the box (mock as final fallback — the app always answers).
- **Hosted users**: plans + credits, monthly budget with 80% soft-warn / 100% hard-stop to mock.
- **Families**: shared memory layer with private-by-default visibility; donor memories read-only to receivers.

## Languages

UI is bilingual EN/RU (`lib/i18n.tsx`, `Localized` message type). The operator/user communicates in Russian; code and docs are English.
