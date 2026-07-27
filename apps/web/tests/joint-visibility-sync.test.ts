// @vitest-environment happy-dom

// Regression test for the joint-family cross-member visibility bug
// ("I still can't see other members' messages in the joint family chat"),
// reported three times after the store-level fix because the root cause was
// client-side, not server-side.
//
// Root cause: the family SCOPE on the send path was read from the separately
// toggleable ``familySessionMode`` store field, while the load path derived
// visibility from the convo id (``convoFamilyVisibility``). ``openConvo``
// (sidebar click) and ``hydrateConvos`` set ``activeConvoId`` but NEVER sync
// ``familySessionMode``, so opening an existing ``fam-joint-`` convo left
// ``familySessionMode`` at its default ``'private'``. The send then stored the
// member's message as PRIVATE under the joint convo id; the other member's
// shared-scope load returned shared events only → saw nothing. The server
// was correct end-to-end (see apps/api/tests/test_joint_visibility_http.py).
//
// Fix: the send path derives ``visibility`` from ``convoFamilyVisibility(cid)``
// (ground truth — the convo it persists into and the load reads), falling
// back to the toggle only for legacy non-prefixed fam convos; and
// ``openConvo`` / ``hydrateConvos`` sync ``familySessionMode`` to the opened
// convo so the toggle UI matches.

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

let streamCalls: Array<{ body: Record<string, unknown> }> = [];
vi.mock('../lib/llm-client', () => ({
  streamChat: vi.fn(async (body: Record<string, unknown>) => {
    streamCalls.push({ body });
  }),
}));

vi.mock('@/lib/vault', async () => {
  const actual = await vi.importActual<typeof import('@/lib/vault')>('@/lib/vault');
  return { ...actual, sealKeyToServer: vi.fn(async () => 'SEALED') };
});

vi.mock('../lib/reset', async () => {
  const actual = await vi.importActual<typeof import('../lib/reset')>('../lib/reset');
  return { ...actual, resetPersonalVault: vi.fn(), resetFamilyVault: vi.fn() };
});

const FAMILY = {
  id: 'fam-1',
  name: 'Test',
  owner_user_id: 'u-me',
  created_at: '2026-07-09T00:00:00Z',
  family_salt: 'AAA=',
  family_enc_blob_seed: null,
  use_owner_personal_key: false,
};
const MEMBERS = [
  {
    family_id: 'fam-1',
    user_id: 'u-me',
    family_role: 'owner' as const,
    family_display_name: 'Alex',
    relation: 'parent',
    color: '#ff6b6b',
    joined_at: '2026-07-09T00:00:00Z',
  },
  {
    family_id: 'fam-1',
    user_id: 'u-other',
    family_role: 'member' as const,
    family_display_name: 'Sam',
    relation: 'child',
    color: '#4dabf7',
    joined_at: '2026-07-09T00:00:00Z',
  },
];

vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: vi.fn(async () => ({
      user_id: 'u-me',
      email: 'me@x.com',
      family_id: 'fam-1',
      family_role: 'owner',
    })),
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
    getFamily: vi.fn(async () => ({
      family: FAMILY,
      members: MEMBERS,
      invites: [],
      provider: {
        id: 'p-fam',
        family_id: 'fam-1',
        kind: 'openai',
        label: 'Family key',
        base_url: null,
        key_handle: 'kh-fam',
        model: 'gpt-4o-mini',
        enc_blob: 'BLOB',
        created_at: '',
      },
    })),
    getFamilyTherapistPrompt: vi.fn(async () => ({
      body: null,
      set_by_user_id: null,
      set_at: null,
      set_by_display_name: null,
    })),
    listProviders: vi.fn(async () => []),
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  streamCalls = [];
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = null;
  if (container?.parentNode) container.parentNode.removeChild(container);
  container = null;
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

async function mountAndSettle(setup?: () => void) {
  await act(async () => {
    root = createRoot(container!);
    root.render(tree());
  });
  await act(async () => {
    for (let i = 0; i < 6; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
  if (setup) {
    act(() => setup());
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

describe('openConvo syncs familySessionMode to the opened convo', () => {
  beforeEach(() => {
    useStore.setState({
      family: FAMILY,
      familyMembers: MEMBERS,
      myUserId: 'u-me',
      activeFamilyMemberId: 'u-me',
      familySessionMode: 'private',
      activeProvider: null,
      activePersonaId: 'fam',
      hydrated: true,
      convos: [
        {
          id: 'fam-joint-fam-1',
          personaId: 'fam',
          title: { en: 'Joint', ru: 'Совместно' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: '', ru: '' },
          msgs: [],
        },
        {
          id: 'fam-solo-u-me-1',
          personaId: 'fam',
          title: { en: 'Solo', ru: 'Соло' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: '', ru: '' },
          msgs: [],
        },
        {
          id: 'c-aria',
          personaId: 'aria',
          title: { en: 'Aria', ru: 'Ария' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: '', ru: '' },
          msgs: [],
        },
      ],
      activeConvoId: 'fam-solo-u-me-1',
    });
  });

  it('opening a fam-joint- convo flips the toggle to shared', () => {
    useStore.getState().openConvo('fam-joint-fam-1');
    expect(useStore.getState().familySessionMode).toBe('shared');
    expect(useStore.getState().activeConvoId).toBe('fam-joint-fam-1');
  });

  it('opening a fam-solo- convo flips the toggle to private', () => {
    // Start from joint to prove it actually changes.
    useStore.setState({ familySessionMode: 'shared' });
    useStore.getState().openConvo('fam-solo-u-me-1');
    expect(useStore.getState().familySessionMode).toBe('private');
  });

  it('opening a non-fam convo leaves familySessionMode untouched', () => {
    useStore.setState({ familySessionMode: 'shared' });
    useStore.getState().openConvo('c-aria');
    expect(useStore.getState().familySessionMode).toBe('shared');
  });
});

describe('ChatScreen send derives visibility from the convo id (ground truth)', () => {
  it('a fam-joint- convo sends visibility=shared even when familySessionMode=private (the desync state)', async () => {
    // The exact failure mode: a member opened the joint thread from the
    // sidebar, so activeConvoId is fam-joint- but familySessionMode is still
    // 'private' (its default). Before the fix the body carried
    // visibility='private' → the message was stored private under the joint
    // convo id → other members saw nothing. The send must derive 'shared'
    // from the convo id and tag the principal as the speaker.
    await mountAndSettle(() => {
      useStore.setState({
        family: FAMILY,
        familyMembers: MEMBERS,
        myUserId: 'u-me',
        activeFamilyMemberId: 'u-me',
        // DESYNC: toggle says private, but the active convo is the joint one.
        familySessionMode: 'private',
        activeProvider: null,
        activePersonaId: 'fam',
        convos: [
          {
            id: 'fam-joint-fam-1',
            personaId: 'fam',
            title: { en: 'Joint', ru: 'Совместно' },
            ts: { en: 'now', ru: 'сейчас' },
            preview: { en: '', ru: '' },
            msgs: [],
          },
        ],
        activeConvoId: 'fam-joint-fam-1',
      });
    });
    await typeAndSend('I feel unheard.');

    expect(streamCalls).toHaveLength(1);
    const body = streamCalls[0]!.body;
    // Visibility comes from the convo id, NOT the stale 'private' toggle.
    expect(body.visibility).toBe('shared');
    expect(body.family_id).toBe('fam-1');
    expect(body.convo_id).toBe('fam-joint-fam-1');
    // Joint speaker is the principal (server requires participant==principal).
    expect(body.participant_user_id).toBe('u-me');
  });

  it('a fam-solo- convo sends visibility=private', async () => {
    await mountAndSettle(() => {
      useStore.setState({
        family: FAMILY,
        familyMembers: MEMBERS,
        myUserId: 'u-me',
        activeFamilyMemberId: 'u-me',
        familySessionMode: 'shared',
        activeProvider: null,
        activePersonaId: 'fam',
        convos: [
          {
            id: 'fam-solo-u-me-1',
            personaId: 'fam',
            title: { en: 'Solo', ru: 'Соло' },
            ts: { en: 'now', ru: 'сейчас' },
            preview: { en: '', ru: '' },
            msgs: [],
          },
        ],
        activeConvoId: 'fam-solo-u-me-1',
      });
    });
    await typeAndSend('just me');

    expect(streamCalls).toHaveLength(1);
    const body = streamCalls[0]!.body;
    // Convo id is solo → private, regardless of the 'shared' toggle state.
    expect(body.visibility).toBe('private');
    expect(body.convo_id).toBe('fam-solo-u-me-1');
  });
});
