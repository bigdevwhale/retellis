// @vitest-environment happy-dom

// Regression test for the "family turn falls through to the mock stand-in"
// bug. The server's ``GET /v1/family`` returns a raw (un-contracted) dict
// with ``providers`` (a LIST) and a ``vault`` sub-object. The client
// ``FamilyState`` expects ``provider`` (singular, nullable). Before this
// fix, ``getFamily()`` returned the raw dict cast to ``FamilyState``, so
// ``fresh.provider`` was ``undefined`` — and BOTH consumers (the auth
// bootstrap ``loadFamily`` and the per-turn ``send()`` refresh) set
// ``familyProvider = undefined``, wiping the key the user had just
// connected. The family turn then had no ``family_enc_key_blob`` and the
// server fell through to the MockAdapter ("offline stand-in — no provider
// key connected").
//
// The fix maps ``providers[0]`` → ``provider`` at the fetch boundary. This
// test exercises the REAL ``getFamily()`` against a server-shaped fetch
// response (not a wholesale mock) so the mapping can't silently regress.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getFamily } from '../lib/api-client';

const originalFetch = globalThis.fetch;

function serverResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const FAMILY = {
  id: 'fam-1',
  name: 'Test',
  owner_user_id: 'u-owner',
  created_at: '2026-07-09T00:00:00Z',
  family_salt: 'AAA=',
  family_enc_blob_seed: null,
};

const MEMBER = {
  family_id: 'fam-1',
  user_id: 'u-owner',
  family_role: 'owner',
  family_display_name: 'Alex',
  relation: 'parent',
  color: '#fff',
  joined_at: '2026-07-09T00:00:00Z',
};

const PROVIDER = {
  id: 'p-1',
  family_id: 'fam-1',
  kind: 'openai',
  label: 'Family',
  base_url: null,
  key_handle: 'kh-1',
  model: 'gpt-4o-mini',
  enc_blob: 'BLOB',
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe('getFamily — server `providers[]` → client `provider` mapping', () => {
  it('maps providers[0] to provider and passes family/members/invites through', async () => {
    // The exact shape the FastAPI handler returns: a `providers` LIST plus
    // a `vault` sub-object the client doesn't consume via getFamily().
    globalThis.fetch = vi.fn(async () =>
      serverResponse({
        family: FAMILY,
        members: [MEMBER],
        invites: [],
        providers: [PROVIDER],
        vault: {
          family_id: 'fam-1',
          vault_initialized: true,
          family_salt: 'AAA=',
          has_provider: true,
        },
      }),
    ) as unknown as typeof fetch;

    const state = await getFamily();

    expect(state.family).toEqual(FAMILY);
    expect(state.members).toEqual([MEMBER]);
    expect(state.invites).toEqual([]);
    // The single provider is the first (and only) family provider.
    expect(state.provider).toEqual(PROVIDER);
    expect(state.provider?.key_handle).toBe('kh-1');
  });

  it('returns provider=null when the family has no providers', async () => {
    globalThis.fetch = vi.fn(async () =>
      serverResponse({
        family: FAMILY,
        members: [],
        invites: [],
        providers: [],
        vault: {
          family_id: 'fam-1',
          vault_initialized: false,
          family_salt: null,
          has_provider: false,
        },
      }),
    ) as unknown as typeof fetch;

    const state = await getFamily();
    expect(state.provider).toBeNull();
  });

  it('exposes the providers list and still hides the vault sub-object', async () => {
    // FamilyState is the client-side view of the family's BYOK surface.
    // Since the multi-key upgrade it carries BOTH the full `providers`
    // list (the multi-key BYOK surface — see FamilySettingsTabs) AND the
    // legacy singular `provider` (the active pointer the chat-side slice
    // reads). The unrelated `vault` sub-object from the server's raw
    // response must NOT leak — that's a separate surface (see
    // `getFamilyVaultMeta`).
    globalThis.fetch = vi.fn(async () =>
      serverResponse({
        family: FAMILY,
        members: [],
        invites: [],
        providers: [PROVIDER],
        vault: {
          family_id: 'fam-1',
          vault_initialized: true,
          family_salt: 'AAA=',
          has_provider: true,
        },
      }),
    ) as unknown as typeof fetch;

    const state = (await getFamily()) as Record<string, unknown>;
    // The singular `provider` (the active pointer the chat-side uses).
    expect(state.provider).toEqual(PROVIDER);
    // The full `providers` list (the multi-key Settings surface).
    expect(state.providers).toEqual([PROVIDER]);
    // The raw `vault` sub-object is NOT exposed on FamilyState — use
    // `getFamilyVaultMeta` for that (separate surface, separate fetch).
    expect(state.vault).toBeUndefined();
  });
});
