// @vitest-environment happy-dom

// Regression test for the error-masking bug in the family therapy chat.
//
// Symptom: sending a family turn showed "Could not reach the companion API.
// Is the backend running on :8000?" even when the backend WAS running —
// because ``streamChat`` threw a bare ``llm/stream → {status}`` for every
// non-2xx, and ``ChatScreen.send()``'s catch discarded the error and showed
// the generic "backend down" string for ALL failures. A server-side 404
// (cross-family guard), 400 (validation), or 500 was indistinguishable
// from a genuine network failure.
//
// The fix: ``streamChat`` now reads the FastAPI ``{"detail": "..."}`` body
// and throws ``llm/stream → {status}: {detail}``. The UI catch keys off the
// ``llm/stream →`` prefix to tell a server response (surface the real
// reason) from a network failure (show the "backend running?" hint).
//
// This test pins the wire contract the UI catch relies on:
//   - 404 with a JSON detail → message includes the status AND the detail.
//   - 500 with no parseable body → message includes the status only.
//   - A network failure (fetch rejects) → rethrown as-is (no ``llm/stream →``
//     prefix), so the UI falls back to the "backend running?" hint.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { streamChat } from '../lib/llm-client';

const originalFetch = globalThis.fetch;

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function mockFetch(res: Response) {
  globalThis.fetch = vi.fn(async () => res) as unknown as typeof fetch;
}

function mockFetchNetworkError() {
  globalThis.fetch = vi.fn(async () => {
    throw new TypeError('Failed to fetch');
  }) as unknown as typeof fetch;
}

describe('streamChat — error surfacing', () => {
  it('a 404 with a JSON detail throws `llm/stream → 404: {detail}`', async () => {
    // Mirrors the cross-family guard: HTTPException(404, "family not found").
    mockFetch(
      new Response(JSON.stringify({ detail: 'family not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(
      streamChat(
        {
          persona_id: 'fam',
          convo_id: 'c1',
          message: 'hi',
          family_id: 'f-stale',
        },
        { onEvent: () => {} },
      ),
    ).rejects.toThrow('llm/stream → 404: family not found');
  });

  it('a 500 with no parseable JSON throws `llm/stream → 500` (status only)', async () => {
    mockFetch(new Response('Internal Server Error', { status: 500 }));

    await expect(
      streamChat({ persona_id: 'fam', convo_id: 'c1', message: 'hi' }, { onEvent: () => {} }),
    ).rejects.toThrow('llm/stream → 500');
  });

  it('a network failure (fetch rejects) rethrows without the `llm/stream →` prefix', async () => {
    // The UI catch uses the ``llm/stream →`` prefix to distinguish a server
    // response from a network failure. A bare TypeError (fetch rejected,
    // backend unreachable) MUST NOT carry that prefix — otherwise the UI
    // would surface "TypeError: Failed to fetch" instead of the "backend
    // running on :8000?" hint.
    mockFetchNetworkError();

    await expect(
      streamChat({ persona_id: 'fam', convo_id: 'c1', message: 'hi' }, { onEvent: () => {} }),
    ).rejects.toThrow('Failed to fetch');
  });
});
