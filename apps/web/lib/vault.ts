'use client';

// One-time ECDH seal of a BYOK API key to the server's session public key.
//
// The client-side zero-knowledge vault (passphrase, master key, IndexedDB,
// per-turn re-seal) is gone. BYOK keys now live server-side, envelope-
// encrypted under a server DEK — the same scheme already used for the
// Telegram bot token. The client sends the plaintext key ONCE at onboarding,
// sealed to the server's X25519 session pubkey (published at GET /v1/health
// → `ecdh_pub`) via libsodium `crypto_box_seal`. The server opens it with its
// session private key and envelope-encrypts it at rest. Per turn the client
// sends `byok_enc_key_blob: null` and the server resolves the key from its
// envelope store.
//
// Honest disclosure (CLAUDE.md / PLAN.md): this is NOT zero-knowledge — the
// server holds the DEK and can decrypt the key at reply time. It protects
// against a database dump, not against the server operator. The plaintext
// key lives only in request memory and is zeroized after the reply
// (immutable heap strings excepted, as ever).
//
// All libsodium use is dynamic + behind a `typeof window` guard so this module
// is safe under SSR and tree-shakes the wasm out of the server bundle.

import type { ProviderKind } from '@ai-companion/contracts';

type Sodium = typeof import('libsodium-wrappers-sumo');

let _sodium: Sodium | null = null;

async function s(): Promise<Sodium> {
  if (typeof window === 'undefined') {
    throw new Error('vault is client-only');
  }
  if (!_sodium) {
    const mod = await import('libsodium-wrappers-sumo');
    // The webpack alias pins libsodium-wrappers-sumo to its CJS build (the ESM
    // build ships a broken import). A dynamic `import()` of a CJS module wraps
    // `module.exports` in a `default` property, so the sodium API is at
    // `mod.default`, not on `mod` directly. Unwrap it; `?? mod` keeps this
    // correct if a future build serves a real ESM namespace.
    const sodium = ((mod as { default?: Sodium }).default ?? mod) as Sodium;
    await sodium.ready;
    _sodium = sodium;
  }
  return _sodium;
}

// --- base64 helpers (browser-safe) ---

function b64encode(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]!);
  return btoa(bin);
}

function b64decode(str: string): Uint8Array {
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// --- key_handle + masking (UI-only metadata; the key itself lives server-side) ---

export function newKeyHandle(): string {
  // Best-effort random handle; not security-critical (the key is stored
  // envelope-encrypted server-side). Kept as an opaque stable pointer so
  // existing rows / UI affordances that read `key_handle` keep working.
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    const bytes = crypto.getRandomValues(new Uint8Array(12));
    return `kh-${b64encode(bytes).replace(/[+/=]/g, '')}`;
  }
  return `kh-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`;
}

export function maskKey(raw: string): string {
  const clean = raw.trim();
  if (clean.length <= 8) return '••••';
  const head = clean.startsWith('sk-') ? 'sk-' : clean.slice(0, 3);
  return `${head}${'•'.repeat(Math.max(4, clean.length - 7))}${clean.slice(-4)}`;
}

// --- ECDH seal (one-time, at onboarding) ---

/** The JSON payload the server's `decrypt_key_blob` / `parse_decrypted_key`
 * expect (see apps/api/.../vault/decrypt.py). Sealing THIS JSON (not a bare
 * api_key) ensures Bedrock's AWS triplet in `extra` survives the trip. */
export type KeyPayload = {
  provider_kind: ProviderKind;
  api_key: string;
  base_url?: string | null;
  // Bedrock needs a 3-field credential triplet (access key id + secret +
  // region) in addition to the api_key. AIHubMix and other OpenAI-compatible
  // gateways don't use it. Server reads it as opaque strings — it never lands
  // in a log.
  extra?: Record<string, string> | null;
};

/** ECDH-seal the plaintext API key (or a JSON envelope {api_key, extra}) to
 * the server's session public key. One-time, at onboarding. The server opens
 * it with its session private key and envelope-encrypts the plaintext at
 * rest under `MESSENGER_TOKEN_DEK`. Returns a base64 `enc_key_blob` ready for
 * `POST /v1/providers` / `POST /v1/family/providers`. */
export async function sealKeyToServer(plaintext: string, serverPubB64: string): Promise<string> {
  const sodium = await s();
  return b64encode(sodium.crypto_box_seal(sodium.from_string(plaintext), b64decode(serverPubB64)));
}
