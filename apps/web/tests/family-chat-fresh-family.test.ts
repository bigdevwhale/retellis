// @vitest-environment happy-dom

// Regression test for the "stale family_id" bug that caused 404 on
// /v1/llm/stream for family turns.
//
// The bug: the family slice in the Zustand store was hydrated on auth
// boot (or on /family mount) and could become stale if the user's family
// membership changed since (e.g. they disbanded + re-created, or accepted
// an invite in another tab). The body of the family chat request read
// `family.id` from the store snapshot, which could disagree with the
// principal's current `family_id` on the server. The server enforces
// the family scope with 404 "family not found" on a mismatch, so the
// user saw a confusing "Failed to connect to companion API" error.
//
// The fix: ChatScreen.send() refreshes the family slice right before
// building the wire body for a family turn. The body's `family_id` is
// then the fresh value, matching the principal.
//
// This test pins the contract:
//   1. With a stale family in the store, sending a family chat
//      calls getFamily() before streamChat().
//   2. The body sent to streamChat carries the FRESH family id, not the
//      stale one. (Mirrors the server's cross-family 404 check.)
//   3. (Phase 1 item 2) The solo member picker is preserved across
//      same-family turns (only reset when the family changes / on first load).
//   4. (Phase 1 item 3) A mid-send family change ABORTS the turn (streamChat
//      NOT called) with a localized in-bubble message — no silent mock.
//   5. (Phase 1 item 4) A non-404 getFamily() error ABORTS the turn with a
//      localized in-bubble message — no stale cross-family body.

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatScreen } from '../components/screens/ChatScreen';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';
import { useStore } from '../lib/store';

const replace = vi.fn();
const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat',
}));

vi.mock('@/lib/speech', () => ({
  useSpeech: () => ({
    supported: false,
    listening: false,
    interim: '',
    autoSpeak: false,
    startListen: vi.fn(),
    stopListen: vi.fn(),
    speak: vi.fn(),
    toggleAutoSpeak: vi.fn(),
  }),
}));

// Capture what streamChat was called with so the test can assert the body shape.
let streamCalls: Array<{ body: Record<string, unknown> }> = [];
vi.mock('../lib/llm-client', () => ({
  streamChat: vi.fn(async (body: Record<string, unknown>) => {
    streamCalls.push({ body });
  }),
}));

// Pre-stub getServerPub() so the BYOK blob build doesn't hit a missing function.
// NOTE: the real ``FamilyState`` (api-client.ts) uses ``provider`` (singular,
// nullable) — NOT ``providers`` (plural). The earlier version of this mock
// used the wrong key, so ``fresh.provider`` was undefined and a missing
// provider refresh in send() was invisible to the test.
let getFamilyReturn: {
  family: {
    id: string;
    name: string;
    owner_user_id: string;
    created_at: string;
    family_salt: string | null;
    family_enc_blob_seed: string | null;
  } | null;
  members: unknown[];
  invites: unknown[];
  provider: {
    id: string;
    family_id: string;
    kind: string;
    label: string;
    base_url: string | null;
    key_handle: string;
    model: string;
    enc_blob: string;
    created_at: string;
  } | null;
} = {
  family: null,
  members: [],
  invites: [],
  provider: null,
};
vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => ({
      user_id: 'u-me',
      email: 'me@x.com',
      family_id: 'f-fresh',
      family_role: 'owner',
    }),
    getAuthConfig: vi.fn(async () => ({
      mode: 'self_hosted',
      profile: 'local',
      auth_backends: ['local'],
      features: {
        billing: false,
        credits: false,
        hosted_fallback: false,
        magic_links: false,
        journal: true,
        shares: true,
      },
    })),
    getHealth: vi.fn(async () => ({ ecdh_pub: 'PUBKEY' })),
    getFamily: vi.fn(async () => getFamilyReturn),
    getFamilyTherapistPrompt: vi.fn(async () => ({
      body: null,
      set_by_user_id: null,
      set_at: null,
      set_by_display_name: null,
    })),
  };
});

// Vault mocks — the family persona path needs sealKeyToServer available if the
// screen ever tries to build a blob, but new clients send `null` and let the
// server resolve the stored key.
vi.mock('../lib/vault', async () => {
  const actual = await vi.importActual<typeof import('@/lib/vault')>('@/lib/vault');
  return {
    ...actual,
    sealKeyToServer: vi.fn(async () => 'SEALED'),
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  streamCalls = [];
  // Default: no family from /v1/family (so loadFamily() on auth boot clears
  // the slice, same as chat-no-key.test.ts). The test sets a "fresh" return
  // value before mount when it wants the send() refresh to see one.
  getFamilyReturn = {
    family: null,
    members: [],
    invites: [],
    provider: null,
  };
  useStore.setState({
    family: null,
    familyProvider: null,
    familyMembers: [],
    activeFamilyMemberId: null,
    familySessionMode: 'private',
    activeProvider: null,
    activePersonaId: 'therapist',
    convos: [],
    activeConvoId: '',
  });
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = null;
  if (container?.parentNode) container.parentNode.removeChild(container);
  container = null;
  replace.mockReset();
  push.mockReset();
});

function tree() {
  return createElement(
    AuthProvider,
    null,
    createElement(
      LangProvider,
      null,
      createElement(Suspense, { fallback: null }, createElement(ChatScreen)),
    ),
  );
}

// Install a family slice with a STALE provider (and optionally a stale
// family id). The test overrides getFamily() to return a fresh family +
// provider so the send() refresh is observable.
function setupFamily(opts: {
  familyId: string;
  keyHandle: string;
  vaultUnlocked?: boolean;
}) {
  useStore.setState({
    activePersonaId: 'fam',
    convos: [],
    activeConvoId: '',
    family: {
      id: opts.familyId,
      name: 'Stale Family',
      owner_user_id: 'u-me',
      created_at: '',
      family_salt: 'SALTSALT==',
      family_enc_blob_seed: 'SEED',
      use_owner_personal_key: false,
    },
    familyMembers: [
      {
        user_id: 'u-me',
        family_id: opts.familyId,
        family_role: 'owner',
        family_display_name: 'Me',
        relation: 'self',
        color: '#7c3aed',
        joined_at: '',
      },
    ],
    familyProvider: {
      id: 'p-1',
      family_id: opts.familyId,
      kind: 'openai',
      label: 'Family',
      base_url: null,
      key_handle: opts.keyHandle,
      model: 'gpt-4o-mini',
      enc_blob: 'BLOB',
    },
    activeFamilyMemberId: 'u-me',
    familySessionMode: 'private',
  });
}

const FRESH_FAMILY_RETURN = {
  family: {
    id: 'f-fresh',
    name: 'Fresh Family',
    owner_user_id: 'u-me',
    created_at: '',
    family_salt: 'SALTSALT==',
    family_enc_blob_seed: 'SEED',
  },
  members: [],
  invites: [],
  provider: {
    id: 'p-fresh',
    family_id: 'f-fresh',
    kind: 'openai',
    label: 'Fresh Family key',
    base_url: null,
    key_handle: 'kh-fresh',
    model: 'gpt-4o-mini',
    enc_blob: 'FRESHBLOB',
    created_at: '',
  },
};

async function mountAndSettle(setup?: () => void) {
  await act(async () => {
    root = createRoot(container!);
    root.render(tree());
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  if (setup) {
    act(() => {
      setup();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  }
}

async function typeAndSend(text: string) {
  const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
  expect(textarea).not.toBeNull();
  await act(async () => {
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      'value',
    )?.set;
    nativeSetter?.call(textarea, text);
    textarea!.dispatchEvent(new Event('input', { bubbles: true }));
    await Promise.resolve();
  });
  const sendBtn = container!.querySelector('button.send') as HTMLButtonElement | null;
  expect(sendBtn).not.toBeNull();
  await act(async () => {
    sendBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('ChatScreen — family chat refreshes the family slice', () => {
  it('same family, refreshed provider: body carries the FRESH family_key_handle, not the stale one', async () => {
    // The send() refresh reads from getFamily() to update the store. We
    // override the return value for the entire test (loadFamily on
    // boot AND the send() refresh). The setupFamily() callback then
    // re-installs a slice with the SAME family id but a STALE provider
    // (kh-1) AFTER the boot clear.
    //
    // The family id is unchanged (f-fresh == f-fresh), so the vault
    // unlock state is preserved — the blob IS built. This isolates the
    // provider-refresh: the body MUST carry the fresh provider's
    // key_handle (kh-fresh), not the stale one (kh-1). The earlier
    // refresh only updated `family` and left `familyProvider` stale, so
    // the body carried a fresh family_id with a stale family_key_handle
    // — and since the server's cross-family guard only checks
    // family_id, the old family's key would silently serve the new
    // family's turn.
    getFamilyReturn = FRESH_FAMILY_RETURN;

    await mountAndSettle(() => setupFamily({ familyId: 'f-fresh', keyHandle: 'kh-1' }));
    await typeAndSend('hi');

    expect(streamCalls).toHaveLength(1);
    const body = streamCalls[0]!.body;
    // family_id is the fresh value (matches the principal's current family).
    expect(body.family_id).toBe('f-fresh');
    // family_key_handle matches the FRESH provider (kh-fresh), not the
    // stale one (kh-1) the store had at mount time.
    expect(body.family_key_handle).toBe('kh-fresh');
    expect(body.family_key_handle).not.toBe('kh-1');
    // And the model on the body comes from the FRESH provider.
    expect(body.model).toBe('gpt-4o-mini');
  });

  it('family changed: aborts the turn (streamChat NOT called), shows a localized bubble, locks the vault', async () => {
    // When the family identity changes between mount and the send()
    // refresh, the in-memory family master key is for the OLD family —
    // using it to build a blob for the NEW family's family_id would
    // send the old family's API key on the new family's turn (the
    // server's cross-family guard only checks family_id, not
    // family_key_handle). send() must NOT silently fall through to the
    // mock stand-in; it must ABORT the turn — reset familyVaultUnlocked,
    // append a plain localized in-bubble message, and return BEFORE
    // building the blob / calling streamChat. No silent mock, no stale
    // cross-family body.
    getFamilyReturn = FRESH_FAMILY_RETURN;

    // Stale slice: family f-stale, vault unlocked, provider kh-stale.
    await mountAndSettle(() => setupFamily({ familyId: 'f-stale', keyHandle: 'kh-stale' }));
    await typeAndSend('hi');

    // streamChat is NOT called — the turn aborted before the wire call.
    expect(streamCalls).toHaveLength(0);
    // A plain localized in-bubble message explains what happened.
    expect(container!.textContent ?? '').toMatch(/Your family changed|Семья сменилась/);
  });

  it('same family, user-picked member X: send() preserves activeFamilyMemberId === X', async () => {
    // Phase 1 item 2: the solo member picker is the user's explicit,
    // per-conversation choice. send() used to reset it to "me" on every
    // turn — snapping the picker back after the user deliberately picked
    // another member. Now it's preserved across same-family turns (only
    // reset when the family changes or on first load when null).
    getFamilyReturn = FRESH_FAMILY_RETURN;

    await mountAndSettle(() => {
      setupFamily({ familyId: 'f-fresh', keyHandle: 'kh-fresh' });
      // The user explicitly picked a DIFFERENT member for this conversation.
      useStore.setState({ activeFamilyMemberId: 'u-other' });
    });
    await typeAndSend('hi');

    // The turn went through (same family -> no abort).
    expect(streamCalls).toHaveLength(1);
    // The picker STAYED on the user's explicit choice — not reset to "me".
    expect(useStore.getState().activeFamilyMemberId).toBe('u-other');
    // And the body's participant_user_id reflects the preserved pick.
    expect(streamCalls[0]!.body.participant_user_id).toBe('u-other');
  });

  it('getFamily() non-404 error: aborts the turn with a localized bubble (no stale body sent)', async () => {
    // Phase 1 item 4: a 5xx / network failure on the per-turn getFamily()
    // refresh must NOT silently proceed with a stale slice (which risks a
    // cross-family body). send() aborts with a localized in-bubble
    // message and returns before streamChat. (404 is the other branch:
    // clear the slice and fall back to the personal path — not asserted
    // here.)
    //
    // The boot loadFamily() (during mount) must succeed so the slice is
    // hydrated; the rejection is reserved for the SECOND call — the
    // send() refresh. mockRejectedValueOnce takes precedence over the
    // ``async () => getFamilyReturn`` implementation, so we install it
    // AFTER mount so the boot call already resolved.
    getFamilyReturn = FRESH_FAMILY_RETURN;
    const { getFamily } = await import('../lib/api-client');

    await mountAndSettle(() => setupFamily({ familyId: 'f-fresh', keyHandle: 'kh-fresh' }));
    // Now the next getFamily() call (the send() refresh) rejects with a 500.
    vi.mocked(getFamily).mockRejectedValueOnce(new Error('llm/stream → 500'));
    await typeAndSend('hi');

    // streamChat NOT called — the turn aborted on the refresh error.
    expect(streamCalls).toHaveLength(0);
    // The slice was NOT cleared (only the 404 branch clears it) — it
    // stays as-is so a retry isn't a cross-family gamble.
    expect(useStore.getState().family?.id).toBe('f-fresh');
    // A plain localized in-bubble message explains the failure.
    expect(container!.textContent ?? '').toMatch(
      /Couldn't refresh your family session|Не удалось обновить семейную сессию/,
    );
  });
});
