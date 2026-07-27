// Map known /v1/llm/stream error messages to plain, localized user-facing
// strings. Phase 3 #13.
//
// The streaming endpoint surfaces server-side rejections as
// `Error.message` strings shaped like ``llm/stream → NNN: detail`` (the
// llm-client prefixes the status + redacted detail). The raw messages
// are fine for a developer but read as cryptic noise to a user — e.g.
// ``llm/stream → 404: family not found`` is the cross-family guard, not
// "the server is down". This module translates the handful of known
// family/session shapes to plain language and returns ``null`` for
// anything it doesn't recognize, so the caller can fall through to its
// own network-failure / generic handling (see ChatScreen.send's catch).
//
// All copy is "disclose, don't perform" — labels only, no affective
// claims. The strings never echo key material or the raw server detail.

import type { Localized } from '@/lib/i18n';

export type L2Fn = (o: Localized) => string;

// Returns a plain localized message for a known llm/stream error, or
// ``null`` if the message isn't a recognized shape (so the caller can
// apply its network-error / generic fallback).
export function explainLlmError(rawMessage: string, l2: L2Fn): string | null {
  const msg = rawMessage ?? '';
  // Only the llm/stream-prefixed server responses are mapped here. A bare
  // "Failed to fetch" / TypeError is a network failure, not an llm/stream
  // response — leave it to the caller's network branch.
  const m = msg.match(/llm\/stream → (\d+)(?::\s*(.*))?/);
  if (!m) return null;
  const status = m[1];
  const detail = (m[2] ?? '').toLowerCase();

  // 404 family not found — the cross-family guard (the family_id on the
  // body doesn't match the principal's current family).
  if (status === '404' && /family not found/.test(detail)) {
    return l2({
      en: 'Your family session changed — reopen the family chat.',
      ru: 'Семейная сессия сменилась — откройте семейный чат заново.',
    });
  }
  // 400 visibility=shared requires family_id — a personal-vs-family scope
  // mismatch on the wire (the body asked for shared recall without a
  // family scope).
  if (status === '400' && /visibility.*shared.*requires.*family/.test(detail)) {
    return l2({
      en: 'Family scope error — reopen the family chat.',
      ru: 'Ошибка области семьи — откройте семейный чат заново.',
    });
  }
  // 400 mutually-exclusive — the body carried both personal and family
  // blobs / scopes, which the server rejects (see the contract's
  // mutually-exclusive rule).
  if (status === '400' && /mutually\s*exclusive/.test(detail)) {
    return l2({
      en: 'Session conflict — start a new family chat.',
      ru: 'Конфликт сессии — начните новый семейный чат.',
    });
  }
  // 429 — rate limited (per-user cost cap on /llm/stream, or the IP burst cap).
  // M2: a localized "try again shortly" beats the generic "code 429". BYOK
  // turns are still per-user-limited (the cap is on identity/cost, not on the
  // key) so this fires for BYOK users too.
  if (status === '429') {
    return l2({
      en: 'Rate limited — please try again shortly.',
      ru: 'Слишком много запросов — попробуйте чуть позже.',
    });
  }

  // Any other llm/stream → NNN: ... — surface the status code without the
  // raw (possibly redacted-but-still-cryptic) detail. The user can quote
  // "code NNN" to whoever is debugging; the detail is dropped.
  return l2({
    en: `Something went wrong (code ${status}).`,
    ru: `Что-то пошло не так (код ${status}).`,
  });
}
