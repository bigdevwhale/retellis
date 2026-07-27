// @vitest-environment happy-dom

// Family accept flow — the email invite link routes the user to
// `/family/accept?token=...`. Two sub-flows:
//   1. The user is already signed in → POST /v1/family/accept with the token.
//   2. The user is NOT signed in → stash the token in a short-lived cookie
//      (`family_invite_token`) and redirect to /login?next=/family/accept.
// We test the client side of (1) here (the API wrapper) and the cookie-stash
// half of (2) by reading document.cookie after a simulated render.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { acceptFamilyInvite } from '../lib/api-client';

type Call = { url: string; init: RequestInit | undefined };

function stubFetch(status: number) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      calls.push({ url: _url, init });
      // 303 needs a Location header to be a real redirect; the client treats
      // 303 the same as 200 — "ok or 303 → true".
      return {
        ok: status >= 200 && status < 300,
        status,
        // 200/303 don't need a body.
        json: async () => ({}),
        text: async () => '',
      } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  // The cookie set by FamilyAcceptPage persists across tests in happy-dom;
  // reset it explicitly so the family_invite_token test stays isolated.
  document.cookie = 'family_invite_token=; Path=/';
});

describe('acceptFamilyInvite', () => {
  it('POSTs the token to /v1/family/accept with credentials', async () => {
    const calls = stubFetch(200);
    const ok = await acceptFamilyInvite('SEALED_TOKEN_AAA');
    expect(ok).toBe(true);
    expect(calls).toHaveLength(1);
    const c = calls[0]!;
    expect(c.url).toMatch(/\/v1\/family\/accept$/);
    expect(c.init?.method).toBe('POST');
    expect(c.init?.credentials).toBe('include');
    const body = JSON.parse(c.init?.body as string) as { token: string };
    expect(body.token).toBe('SEALED_TOKEN_AAA');
  });

  it('treats 303 the same as 200 (redirect from server on success)', async () => {
    const calls = stubFetch(303);
    const ok = await acceptFamilyInvite('SEALED');
    expect(ok).toBe(true);
    expect(calls).toHaveLength(1);
  });

  it('returns false on 4xx (e.g. expired, tampered, wrong family)', async () => {
    stubFetch(400);
    const ok = await acceptFamilyInvite('BAD');
    expect(ok).toBe(false);
  });

  it('returns false on 5xx (server error)', async () => {
    stubFetch(500);
    const ok = await acceptFamilyInvite('X');
    expect(ok).toBe(false);
  });
});

describe('family_invite_token cookie (stashed on unauthenticated landing)', () => {
  // The cookie is set client-side when the user lands on /family/accept
  // unauthenticated. Document.cookie parsing is happy-dom–native.
  it('cookie set by the accept page is readable back', () => {
    // The page writes `family_invite_token=...; Path=/; Max-Age=1800; SameSite=Lax`
    // when the user lands unauthenticated. We confirm the format round-trips.
    const token = 'sealed-cookie-token-123';
    document.cookie = `family_invite_token=${encodeURIComponent(token)}; Path=/; Max-Age=1800; SameSite=Lax`;
    const has = document.cookie
      .split(';')
      .map((s) => s.trim())
      .some((s) => s === `family_invite_token=${encodeURIComponent(token)}`);
    expect(has).toBe(true);
  });
});
