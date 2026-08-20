# Security, told straight

This is the canonical, honest description of how Retellis stores and handles your
API keys. The in-app UI intentionally keeps key copy short and neutral; the full
detail lives here.

The security model is a feature, not an apology. The load-bearing points:

- **Keys are never stored in plaintext.** BYOK API keys and the Telegram `bot_token`
  are stored as envelope ciphertext (`api_key_ciphertext`, NaCl `SecretBox` under
  `MESSENGER_TOKEN_DEK`). The contract `Provider` / `FamilyProvider` models never
  carry the ciphertext — only a `key_handle` is returned to the client.
- **Sealed once in transit.** Onboarding ECDH-seals the plaintext key to the
  server's session public key (`/v1/health` → `ecdh_pub`, `crypto_box_seal`). The
  server opens it with its session ECDH private key, envelope-encrypts, stores the
  ciphertext, drops the plaintext.
- **Zeroized per reply.** The decrypted key is resolved in memory inside
  `vault/zeroize.py`'s `zeroized()` context manager and the source `bytearray` is
  wiped after the call — on success *and* on exception.
- **`grep -r 'sk-' deploy/` returns nothing.** `observability/redaction.py` scrubs
  `sk-...` patterns from all logs and Langfuse metadata, gated by
  `tests/test_redaction.py`. Every string surfaced to the client or logs passes
  through `redact()`.
- **Per-provider model selection.** `Provider.model` is sent per request, so a
  BYOK candidate uses the user's chosen model; env-fallback and Ollama nodes keep
  server defaults.

## Honest zeroize disclosure (don't overstate)

Once the plaintext key is read into an immutable Python `str` / JS `string` for
the single LiteLLM call, those characters live on the managed heap and cannot be
wiped by us. The source buffer is wiped; the key is never persisted, logged, or
traced. We do not claim "erased from all memory."

## Honest scope disclosure (the important part)

The server **holds** `MESSENGER_TOKEN_DEK` and **can** decrypt BYOK keys at reply
time. **This is not zero-knowledge.** It protects against a database dump, not
against the server operator. Because keys live on the server, they survive a
browser-data wipe and work across your devices. If you want full key custody,
self-host and hold the DEK yourself.

The in-app UI therefore does not promise confidentiality it can't guarantee. It
says only that your key is *encrypted in transit and at rest on the server* —
which is true — and points here for the full picture. We'd rather tell you the
truth than sell you a lie.

## Where to find the rest

- Backend security invariants and redaction rules: [`CLAUDE.md`](.claude/CLAUDE.md)
- Key precedence, fallback chain, and BYOK storage scheme: [`README.md`](README.md)
- Visual and voice contract: [`DESIGN.md`](DESIGN.md)