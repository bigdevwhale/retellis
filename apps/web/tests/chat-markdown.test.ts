// @vitest-environment happy-dom

// Regression test for markdown rendering in assistant chat bubbles. The
// companion's LLM replies carry markdown (``**bold**``, lists, `` `code` ``),
// and the chat used to render them as plain text — so the user saw literal
// asterisks instead of bold. The fix wraps the ``them``-bubble body and the
// streaming bubble in <Markdown> (react-markdown + remark-gfm). This test
// pins that ``**bold**`` becomes a real <strong>, `` `code` `` a real <code>,
// and that the raw ``**`` never reaches the visible DOM as text.
//
// Also covers ``stripMarkdown``: the TTS path must not hand ``**`` to
// SpeechSynthesisUtterance (the user would hear "asterisk asterisk").

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatScreen } from '../components/screens/ChatScreen';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';
import { stripMarkdown } from '../lib/markdown';
import { useStore } from '../lib/store';

const replace = vi.fn();
const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat',
}));

// speech is observed by ChatScreen; stub it so we don't touch the real
// SpeechSynthesis API in the happy-dom env.
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
  return {
    ...actual,
    sealKeyToServer: vi.fn(async () => 'SEALED'),
  };
});

const validMe = {
  user_id: 'u-me',
  email: 'me@x.com',
  family_id: 'f-1',
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
        journal: true,
        shares: true,
      },
    })),
    getHealth: vi.fn(async () => ({ ecdh_pub: 'PUBKEY' })),
    getFamily: vi.fn(async () => ({ family: null, members: [], invites: [], providers: [] })),
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
  // Seed a personal convo with one assistant bubble carrying markdown.
  // ``hydrated: true`` short-circuits hydrateConvos() so it doesn't fire
  // a real fetch in the happy-dom env (best-effort anyway, but skipping
  // keeps the test hermetic).
  useStore.setState({
    family: null,
    familyProvider: null,
    familyMembers: [],
    activeFamilyMemberId: null,
    familySessionMode: 'private',
    activeProvider: null,
    activePersonaId: 'aria',
    activeConvoId: 'c-1',
    hydrated: true,
    convos: [
      {
        id: 'c-1',
        personaId: 'aria',
        title: { en: 'Markdown', ru: 'Markdown' },
        ts: { en: 'now', ru: 'сейчас' },
        preview: { en: '', ru: '' },
        msgs: [
          {
            them: true,
            t: {
              en: '**bold** and `code` and *italic* with a [link](https://x.example).',
              ru: '**жирный** и `код` и *курсив* со [ссылкой](https://x.example).',
            },
            ts: '00:00',
          },
        ],
      },
    ],
  });
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
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('ChatScreen — assistant markdown rendering', () => {
  it('renders **bold** as <strong>, `code` as <code>, *italic* as <em>', async () => {
    await mountAndSettle();

    const themBody = container!.querySelector('.msg.them .body') as HTMLElement | null;
    expect(themBody).not.toBeNull();

    expect(themBody!.querySelector('strong')?.textContent).toBe('bold');
    expect(themBody!.querySelector('code')?.textContent).toBe('code');
    expect(themBody!.querySelector('em')?.textContent).toBe('italic');
  });

  it('renders a GFM link with target=_blank and rel=noopener', async () => {
    await mountAndSettle();

    const link = container!.querySelector('.msg.them .body a') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    expect(link!.getAttribute('href')).toBe('https://x.example');
    expect(link!.getAttribute('target')).toBe('_blank');
    expect(link!.rel).toContain('noopener');
    expect(link!.textContent).toBe('link');
  });

  it('does not leak raw markdown markers into the visible text', async () => {
    await mountAndSettle();

    const themBody = container!.querySelector('.msg.them .body') as HTMLElement | null;
    expect(themBody).not.toBeNull();
    const text = themBody!.textContent ?? '';
    // The ``**`` / ``` `` / ``*`` / ``[...]`` markup must be consumed by the
    // renderer, not shown literally to the user.
    expect(text).not.toContain('**');
    expect(text).not.toContain('`');
    expect(text).not.toContain('[');
    expect(text).toContain('bold');
    expect(text).toContain('code');
  });

  it('renders a fenced code block and a bulleted list', async () => {
    useStore.setState({
      convos: [
        {
          id: 'c-1',
          personaId: 'aria',
          title: { en: 'Markdown', ru: 'Markdown' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: '', ru: '' },
          msgs: [
            {
              them: true,
              t: {
                en: 'Steps:\n\n- one\n- two\n\n```\nprint("hi")\n```',
                ru: 'Шаги:\n\n- один\n- два\n\n```\nprint("hi")\n```',
              },
              ts: '00:00',
            },
          ],
        },
      ],
    });
    await mountAndSettle();

    const themBody = container!.querySelector('.msg.them .body') as HTMLElement | null;
    expect(themBody).not.toBeNull();
    const list = themBody!.querySelector('ul');
    expect(list).not.toBeNull();
    expect(list!.querySelectorAll('li').length).toBe(2);
    const pre = themBody!.querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('print("hi")');
  });
});

describe('stripMarkdown', () => {
  it('drops emphasis, code, links, and list markers for TTS', () => {
    expect(stripMarkdown('**a** and `b` and *c*')).toBe('a and b and c');
    expect(stripMarkdown('see [here](https://x.example) now')).toBe('see here now');
    expect(stripMarkdown('# Heading\n- one\n- two')).toBe('Heading\none\ntwo');
    expect(stripMarkdown('done ~~old~~ new')).toBe('done old new');
    expect(stripMarkdown('```\nprint(1)\n```')).toBe('print(1)');
  });

  it('does not mangle snake_case words', () => {
    // Single underscores inside a word are NOT italic markers; the cleaner
    // keeps them so ``some_var_name`` doesn't become ``somevarname``.
    expect(stripMarkdown('use some_var_name here')).toBe('use some_var_name here');
  });
});
