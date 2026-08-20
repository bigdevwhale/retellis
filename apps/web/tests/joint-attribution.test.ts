// @vitest-environment happy-dom

// Joint family therapy thread: each message must be attributed to the member
// who wrote it, and other members' messages must NOT render as the viewer's
// own right-aligned "me" bubble. The server tags every user event with
// ``participant_user_id`` (the speaker; None on assistant events); the store
// carries it through to ``Message.speakerUserId`` and the joint renderer
// emits a ``.msg-author`` caption + re-aligns other members left as
// ``.msg.other``. Personal / solo / non-fam chats render no caption.

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

vi.mock('@/lib/llm-client', () => ({
  streamChat: vi.fn(async () => {}),
}));

vi.mock('../lib/reset', async () => {
  const actual = await vi.importActual<typeof import('../lib/reset')>('../lib/reset');
  return { ...actual, resetPersonalVault: vi.fn(), resetFamilyVault: vi.fn() };
});

vi.mock('@/lib/vault', async () => {
  const actual = await vi.importActual<typeof import('@/lib/vault')>('@/lib/vault');
  return { ...actual, sealKeyToServer: vi.fn(async () => 'SEALED') };
});

const validMe = {
  user_id: 'u-me',
  email: 'me@x.com',
  family_id: 'fam-1',
  family_role: 'owner',
};

vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: vi.fn(async () => validMe),
    getAuthConfig: vi.fn(async () => ({
      mode: 'self_hosted',
      profile: 'local',
      auth_backends: ['local'],
      features: {
        billing: false,
        credits: false,
        hosted_fallback: false,
        magic_links: false,
        email_verification: false,
        journal: true,
        shares: true,
      },
    })),
    getHealth: vi.fn(async () => ({ ecdh_pub: 'PUBKEY' })),
    // AuthProvider calls loadFamily(me.user_id) on mount (auth.tsx), which
    // overwrites family/familyMembers from this response — so return the real
    // family + members or the speaker lookup in the renderer comes up empty.
    getFamily: vi.fn(async () => ({
      family: FAMILY,
      members: MEMBERS,
      invites: [],
      providers: [],
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

const FAMILY = {
  id: 'fam-1',
  name: 'Test',
  owner_user_id: 'u-me',
  created_at: '2026-07-09T00:00:00Z',
  family_salt: 'AAA=',
  family_enc_blob_seed: null,
  use_owner_personal_key: false,
};
// Two members with distinct colors so the test can assert the per-member
// accent is applied to other-member bubbles only.
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

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
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

async function mountAndSettle() {
  await act(async () => {
    root = createRoot(container!);
    root.render(tree());
  });
  // Flush the auth→loadFamily chain (getMe → loadFamily → getFamily +
  // therapist prompt → set family slice) so the renderer sees the members
  // before assertions. A few microtask + macrotask hops cover the awaits.
  await act(async () => {
    for (let i = 0; i < 6; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe('ChatScreen — joint thread author attribution', () => {
  beforeEach(() => {
    useStore.setState({
      family: FAMILY,
      familyProvider: null,
      familyMembers: MEMBERS,
      activeFamilyMemberId: 'u-me',
      myUserId: 'u-me',
      familySessionMode: 'shared',
      activeProvider: null,
      activePersonaId: 'fam',
      activeConvoId: 'fam-joint-fam-1',
      hydrated: true,
      convos: [
        {
          id: 'fam-joint-fam-1',
          personaId: 'fam',
          title: { en: 'Joint', ru: 'Совместно' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: '', ru: '' },
          msgs: [
            {
              them: false,
              t: { en: 'I feel unheard.', ru: 'Меня не слышат.' },
              ts: '00:00',
              speakerUserId: 'u-other',
            },
            {
              them: false,
              t: { en: 'I hear you.', ru: 'Я тебя слышу.' },
              ts: '00:01',
              speakerUserId: 'u-me',
            },
            {
              them: true,
              t: { en: 'Let’s talk through it.', ru: 'Давай обсудим.' },
              ts: '00:02',
            },
          ],
        },
      ],
    });
  });

  it('labels every bubble: other member, local user, therapist', async () => {
    await mountAndSettle();

    const authors = container!.querySelectorAll('.msg-author');
    // All three bubbles are captioned in a joint thread.
    expect(authors).toHaveLength(3);

    // Other member → left-aligned `.msg.other`, caption = their display name,
    // and the per-member color accent rides the bubble as a CSS custom prop.
    const other = container!.querySelector('.msg.other') as HTMLElement | null;
    expect(other).not.toBeNull();
    expect(other!.querySelector('.msg-author')?.textContent).toBe('Sam');
    expect(other!.style.getPropertyValue('--member-color')).toBe('#4dabf7');
    // The caption text itself is colored with the member color (inline style).
    const otherAuthor = other!.querySelector('.msg-author') as HTMLElement;
    expect(otherAuthor.style.color).toBe('#4dabf7');

    // Local user → right-aligned `.msg.me`, caption = their own display name.
    const me = container!.querySelector('.msg.me') as HTMLElement | null;
    expect(me).not.toBeNull();
    expect(me!.querySelector('.msg-author')?.textContent).toBe('Alex');

    // Therapist → `.msg.them`, caption = the persona's localized role.
    const them = container!.querySelector('.msg.them') as HTMLElement | null;
    expect(them).not.toBeNull();
    expect(them!.querySelector('.msg-author')?.textContent).toBe('Family therapist');
  });

  it('does not show the journal-seed action on another member’s bubble', async () => {
    // The "save to journal" affordance seeds the composer with the message
    // text — it must only appear on the viewer's own user bubbles, never on
    // another member's disclosure (you can't journal someone else's words).
    await mountAndSettle();

    const other = container!.querySelector('.msg.other') as HTMLElement | null;
    expect(other).not.toBeNull();
    expect(other!.querySelector('.msg-me-actions')).toBeNull();
    // The viewer's own bubble still has it.
    const me = container!.querySelector('.msg.me') as HTMLElement | null;
    expect(me!.querySelector('.msg-me-actions')).not.toBeNull();
  });
});

describe('ChatScreen — attribution is joint-only', () => {
  it('renders no caption in a personal (aria) convo', async () => {
    useStore.setState({
      family: null,
      familyMembers: [],
      activeFamilyMemberId: null,
      myUserId: null,
      activeProvider: null,
      activePersonaId: 'aria',
      activeConvoId: 'c-aria',
      hydrated: true,
      convos: [
        {
          id: 'c-aria',
          personaId: 'aria',
          title: { en: 'Aria', ru: 'Ария' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: '', ru: '' },
          msgs: [
            { them: true, t: { en: 'Hi there.', ru: 'Привет.' }, ts: '00:00' },
            { them: false, t: { en: 'Hello.', ru: 'Привет.' }, ts: '00:01' },
          ],
        },
      ],
    });
    await mountAndSettle();
    expect(container!.querySelectorAll('.msg-author')).toHaveLength(0);
    expect(container!.querySelector('.msg.other')).toBeNull();
  });

  it('renders no caption in a solo (fam-solo-) family convo', async () => {
    useStore.setState({
      family: FAMILY,
      familyMembers: MEMBERS,
      activeFamilyMemberId: 'u-me',
      myUserId: 'u-me',
      familySessionMode: 'private',
      activeProvider: null,
      activePersonaId: 'fam',
      activeConvoId: 'fam-solo-me-1',
      hydrated: true,
      convos: [
        {
          id: 'fam-solo-me-1',
          personaId: 'fam',
          title: { en: 'Solo', ru: 'Индивид.' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: '', ru: '' },
          msgs: [
            { them: true, t: { en: 'How are you?', ru: 'Как ты?' }, ts: '00:00' },
            {
              them: false,
              t: { en: 'Okay.', ru: 'Нормально.' },
              ts: '00:01',
              speakerUserId: 'u-me',
            },
          ],
        },
      ],
    });
    await mountAndSettle();
    // Solo is a private 1:1 — no author captions, no `.msg.other`.
    expect(container!.querySelectorAll('.msg-author')).toHaveLength(0);
    expect(container!.querySelector('.msg.other')).toBeNull();
  });
});
