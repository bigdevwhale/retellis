// Unit tests for the llm-stream error → plain-message mapper (Phase 3 #13).
//
// The streaming endpoint surfaces server-side rejections as
// `Error.message` strings shaped like ``llm/stream → NNN: detail``. The
// raw strings read as cryptic noise to a user; explainLlmError maps the
// known family/session shapes to plain localized messages and returns
// ``null`` for anything it doesn't recognize (so the caller can fall
// through to its network-failure / generic handling).
//
// The L2 fn here is the real i18n one's contract: ``(o: { en; ru }) =>
// o[lang]``. We drive it for both langs to assert both halves localize.

import { describe, expect, it } from 'vitest';
import type { Localized } from '../lib/i18n';
import { explainLlmError } from '../lib/llm-errors';

const enL2 = (o: Localized) => o.en;
const ruL2 = (o: Localized) => o.ru;

describe('explainLlmError — maps known llm/stream errors to plain messages', () => {
  it('404 family not found → "Your family session changed"', () => {
    const msg = 'llm/stream → 404: family not found';
    expect(explainLlmError(msg, enL2)).toMatch(/family session changed/i);
    expect(explainLlmError(msg, ruL2)).toMatch(/сессия сменилась/i);
  });

  it('400 visibility=shared requires family_id → "Family scope error"', () => {
    const msg = 'llm/stream → 400: visibility=shared requires family_id';
    expect(explainLlmError(msg, enL2)).toMatch(/family scope error/i);
    expect(explainLlmError(msg, ruL2)).toMatch(/области семьи/i);
  });

  it('400 mutually exclusive → "Session conflict"', () => {
    const msg = 'llm/stream → 400: enc_key_blob and family_enc_key_blob are mutually exclusive';
    expect(explainLlmError(msg, enL2)).toMatch(/session conflict/i);
    expect(explainLlmError(msg, ruL2)).toMatch(/конфликт сессии/i);
  });

  it('429 → localized "Rate limited" (not the generic code message)', () => {
    const msg = 'llm/stream → 429: too many requests';
    expect(explainLlmError(msg, enL2)).toMatch(/rate limited/i);
    expect(explainLlmError(msg, enL2)).not.toMatch(/code 429/i);
    expect(explainLlmError(msg, ruL2)).toMatch(/попробуйте чуть позже/i);
  });

  it('other llm/stream → NNN → generic "code NNN" message (raw detail dropped)', () => {
    const msg = 'llm/stream → 500: internal server error';
    expect(explainLlmError(msg, enL2)).toMatch(/something went wrong \(code 500\)/i);
    // The raw (potentially-redacted-but-cryptic) detail is NOT echoed.
    expect(explainLlmError(msg, enL2)).not.toContain('internal server error');
    expect(explainLlmError(msg, ruL2)).toMatch(/код 500/);
  });

  it('a generic 400 with no recognized detail falls through to the code message', () => {
    const msg = 'llm/stream → 400: some other validation failure';
    expect(explainLlmError(msg, enL2)).toMatch(/something went wrong \(code 400\)/i);
  });

  it('returns null for a real network failure (TypeError "Failed to fetch")', () => {
    // Not an llm/stream response — the caller's network branch handles it.
    expect(explainLlmError('Failed to fetch', enL2)).toBeNull();
  });

  it('returns null for a non-llm/stream error string', () => {
    expect(explainLlmError('something weird happened', enL2)).toBeNull();
    expect(explainLlmError('', enL2)).toBeNull();
  });

  it('returns null for undefined / null so the caller falls through', () => {
    expect(explainLlmError(undefined as unknown as string, enL2)).toBeNull();
  });
});
