// @vitest-environment happy-dom

// Regression test for the no-key lockout in the chat composer. With the
// client-side vault gone, "no key" now means "no provider row on the server".
// The lockout banner routes the user to the right settings page and disables
// the composer so the server-fallback stand-in is not reachable by accident.
// A secondary "Reset? Wipe keys" affordance lets owners delete stale
// server-side provider rows without navigating away.

import type { AuthConfig } from '@ai-companion/contracts';
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

let resetPersonalVaultMock = vi.fn(async () => undefined);
let resetFamilyVaultMock = vi.fn(async () => ({
  providersDeleted: 1,
  serverSeedOk: true,
}));
vi.mock('../lib/reset', async () => {
  const actual = await vi.importActual<typeof import('../lib/reset')>('../lib/reset');
  return {
    ...actual,
    resetPersonalVault: (...args: unknown[]) =>
      resetPersonalVaultMock(...(args as Parameters<typeof resetPersonalVaultMock>)),
    resetFamilyVault: (...args: unknown[]) =>
      resetFamilyVaultMock(...(args as Parameters<typeof resetFamilyVaultMock>)),
  };
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
  family_role: 'owner' as const,
};

let getMeShouldThrow = false;
let listProvidersMock = vi.fn(async () => [] as never[]);
// Swappable auth config so we can exercise both self-hosted (hard lockout)
// and hosted (lazy onboarding soft nudge). The lockout derivation keys on
// `mode`, not `features.billing` — see the hosted describe block below.
let getAuthConfigMock: () => Promise<AuthConfig> = vi.fn<() => Promise<AuthConfig>>(async () => ({
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
}));
vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => {
      if (getMeShouldThrow) throw new Error('unauth');
      return validMe;
    },
    getAuthConfig: () => getAuthConfigMock(),
    getHealth: vi.fn(async () => ({ ecdh_pub: 'PUBKEY' })),
    getFamily: vi.fn(async () => ({
      family: null,
      members: [],
      invites: [],
      providers: [],
    })),
    getFamilyTherapistPrompt: vi.fn(async () => ({
      body: null,
      set_by_user_id: null,
      set_at: null,
      set_by_display_name: null,
    })),
    listProviders: (...args: unknown[]) =>
      listProvidersMock(...(args as Parameters<typeof listProvidersMock>)),
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
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
  getMeShouldThrow = false;
  listProvidersMock = vi.fn(async () => [] as never[]);
  getAuthConfigMock = vi.fn<() => Promise<AuthConfig>>(async () => ({
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
  }));
  resetPersonalVaultMock = vi.fn(async () => undefined);
  resetFamilyVaultMock = vi.fn(async () => ({
    providersDeleted: 1,
    serverSeedOk: true,
  }));
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

function setupFamilyPersona(extra: Record<string, unknown> = {}) {
  useStore.setState({
    activePersonaId: 'fam',
    convos: [],
    activeConvoId: '',
    family: {
      id: 'f-1',
      name: 'Test',
      owner_user_id: 'u-me',
      created_at: '',
      family_salt: null,
      family_enc_blob_seed: null,
      use_owner_personal_key: false,
    },
    familyMembers: [
      {
        user_id: 'u-me',
        family_id: 'f-1',
        family_role: 'owner',
        family_display_name: 'Me',
        relation: 'self',
        color: '#7c3aed',
        joined_at: '',
      },
    ],
    activeFamilyMemberId: 'u-me',
    familySessionMode: 'private',
    ...extra,
  });
}

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

describe('ChatScreen — no-key lockout', () => {
  it('disables the composer for the family persona when no family provider exists', async () => {
    await mountAndSettle(() => {
      setupFamilyPersona({ familyProvider: null });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea).not.toBeNull();
    expect(textarea!.disabled).toBe(true);

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
    const link = banner!.querySelector('a') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    expect(link!.getAttribute('href')).toBe('/family?tab=settings&subtab=key');
  });

  it('enables the composer when the family provider is configured', async () => {
    await mountAndSettle(() => {
      setupFamilyPersona({
        familyProvider: {
          id: 'p-1',
          family_id: 'f-1',
          kind: 'openai',
          label: 'Family',
          base_url: null,
          key_handle: 'kh-1',
          model: 'gpt-4o-mini',
          enc_blob: null,
          created_at: '',
        },
      });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea).not.toBeNull();
    expect(textarea!.disabled).toBe(false);
    expect(container!.querySelector('.chat-locked-banner')).toBeNull();
  });

  it('disables the composer for the family persona when not in a family', async () => {
    await mountAndSettle(() => {
      useStore.setState({
        activePersonaId: 'fam',
        convos: [],
        activeConvoId: '',
        family: null,
        familyProvider: null,
      });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea!.disabled).toBe(true);

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
    const link = banner!.querySelector('a');
    expect(link!.getAttribute('href')).toBe('/family');
  });

  it('disables the composer for the personal persona when no BYOK key is active', async () => {
    await mountAndSettle(() => {
      useStore.setState({
        activePersonaId: 'aria',
        family: null,
        familyProvider: null,
        activeProvider: null,
      });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea!.disabled).toBe(true);

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
    const link = banner!.querySelector('a') as HTMLAnchorElement | null;
    expect(link!.getAttribute('href')).toBe('/onboarding');
  });

  it('enables the composer when the personal provider is configured', async () => {
    await mountAndSettle(() => {
      useStore.setState({
        activePersonaId: 'aria',
        family: null,
        familyProvider: null,
        activeProvider: {
          providerId: 'p-1',
          kind: 'openai',
          label: 'Personal',
          keyHandle: 'kh-1',
          model: 'gpt-4o-mini',
        },
      });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea!.disabled).toBe(false);
    expect(container!.querySelector('.chat-locked-banner')).toBeNull();
  });

  it('shows a "Reset? Wipe keys" button in the family no-key banner for the owner', async () => {
    await mountAndSettle(() => {
      setupFamilyPersona({ familyProvider: null });
    });

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
    const buttons = Array.from(banner!.querySelectorAll('button'));
    const resetBtn = buttons.find((b) => /Reset\?|Сбросить\?/i.test(b.textContent ?? ''));
    expect(resetBtn).toBeDefined();
  });

  it('does NOT show a reset button in the family no-key banner for non-owners', async () => {
    await mountAndSettle(() => {
      setupFamilyPersona({
        family: {
          id: 'f-1',
          name: 'Test',
          owner_user_id: 'u-someone-else',
          created_at: '',
          family_salt: null,
          family_enc_blob_seed: null,
        },
        familyProvider: null,
      });
    });

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
    const buttons = Array.from(banner!.querySelectorAll('button'));
    const resetBtn = buttons.find((b) => /Reset\?|Сбросить\?/i.test(b.textContent ?? ''));
    expect(resetBtn).toBeUndefined();
  });

  it('personal no-key banner has a reset button that triggers resetPersonalVault', async () => {
    await mountAndSettle(() => {
      useStore.setState({
        activePersonaId: 'aria',
        family: null,
        familyProvider: null,
        activeProvider: null,
      });
    });

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    const buttons = Array.from(banner!.querySelectorAll('button'));
    const resetBtn = buttons.find((b) => /Reset\?|Сбросить\?/i.test(b.textContent ?? ''));
    expect(resetBtn).toBeDefined();

    await act(async () => {
      resetBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    const resetInput = banner!.querySelector(
      'input[aria-label="Type RESET"], input[aria-label="Введите RESET"]',
    ) as HTMLInputElement | null;
    expect(resetInput).not.toBeNull();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(resetInput, 'RESET');
      resetInput!.dispatchEvent(new Event('input', { bubbles: true }));
      await Promise.resolve();
    });
    const form = resetInput!.closest('form') as HTMLFormElement | null;
    await act(async () => {
      form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(resetPersonalVaultMock).toHaveBeenCalledTimes(1);
  });

  it('family reset confirm calls resetFamilyVault when the family name is typed correctly', async () => {
    await mountAndSettle(() => {
      setupFamilyPersona({ familyProvider: null });
    });

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    const buttons = Array.from(banner!.querySelectorAll('button'));
    const resetBtn = buttons.find((b) => /Reset\?|Сбросить\?/i.test(b.textContent ?? ''));
    expect(resetBtn).toBeDefined();

    await act(async () => {
      resetBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    const phraseInput = banner!.querySelector(
      'input[aria-label*="family name"], input[aria-label*="имя семьи"]',
    ) as HTMLInputElement | null;
    expect(phraseInput).not.toBeNull();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      setter?.call(phraseInput, 'Test');
      phraseInput!.dispatchEvent(new Event('input', { bubbles: true }));
      await Promise.resolve();
    });
    const form = phraseInput!.closest('form') as HTMLFormElement | null;
    await act(async () => {
      form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(resetFamilyVaultMock).toHaveBeenCalledTimes(1);
  });

  it('shows the model in the head when a provider is set, "no key" otherwise', async () => {
    await mountAndSettle(() => {
      setupFamilyPersona({ familyProvider: null });
    });

    const head = container!.querySelector('.chat-head');
    const text = (head?.textContent ?? '').toLowerCase();
    expect(text).not.toContain('stand-in');
    expect(text).not.toContain('заглушка');
    expect(text).toMatch(/no key|нет ключа/);
  });
});

describe('ChatScreen — hosted lazy onboarding (soft nudge, not lockout)', () => {
  beforeEach(() => {
    // Hosted mode: a missing *personal* key is not a hard lockout — the
    // routing chain falls through to the operator env fallback (trial credits
    // / OpenRouter) or MockAdapter, so the app always answers. The composer
    // stays enabled and a soft nudge shows. Keyed on `mode === 'hosted'`, NOT
    // `features.billing` — the trial path works with billing off.
    getAuthConfigMock = vi.fn<() => Promise<AuthConfig>>(async () => ({
      mode: 'hosted',
      profile: 'local',
      auth_backends: ['local'],
      features: {
        billing: true,
        credits: true,
        hosted_fallback: true,
        magic_links: false,
        email_verification: false,
        journal: true,
        shares: true,
      },
    }));
  });

  it('keeps the personal composer enabled with no BYOK key and shows a soft nudge (no reset button)', async () => {
    await mountAndSettle(() => {
      useStore.setState({
        activePersonaId: 'aria',
        family: null,
        familyProvider: null,
        activeProvider: null,
      });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea).not.toBeNull();
    // Composer is ENABLED on hosted — chat works via env/mock fallback.
    expect(textarea!.disabled).toBe(false);

    // The soft nudge banner renders and links to onboarding.
    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
    const link = banner!.querySelector('a') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    expect(link!.getAttribute('href')).toBe('/onboarding');

    // No scary "Reset? Wipe keys" affordance on a soft nudge — the user
    // simply hasn't added a key yet.
    const buttons = Array.from(banner!.querySelectorAll('button'));
    const resetBtn = buttons.find((b) => /Reset\?|Сбросить\?/i.test(b.textContent ?? ''));
    expect(resetBtn).toBeUndefined();
  });

  it('still hard-locks the family persona with no family key on hosted (a shared key is not deferrable)', async () => {
    await mountAndSettle(() => {
      setupFamilyPersona({ familyProvider: null });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea!.disabled).toBe(true);

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
  });

  it('enables the composer on hosted mode even when billing is OFF (trial-credits path: operator env fallback)', async () => {
    // Regression: the live hosted server runs FEATURE_BILLING=0 +
    // FEATURE_CREDITS=1 — trial credits are served by the operator-paid
    // OpenRouter env fallback, no billing provider configured. `hosted` must
    // be keyed on `mode === 'hosted'`, not `features.billing`, or a fresh
    // hosted signup is hard-locked out of chat.
    getAuthConfigMock = vi.fn<() => Promise<AuthConfig>>(async () => ({
      mode: 'hosted',
      profile: 'local',
      auth_backends: ['local'],
      features: {
        billing: false,
        credits: true,
        hosted_fallback: false,
        magic_links: false,
        email_verification: false,
        journal: true,
        shares: true,
      },
    }));
    await mountAndSettle(() => {
      useStore.setState({
        activePersonaId: 'aria',
        family: null,
        familyProvider: null,
        activeProvider: null,
      });
    });

    const textarea = container!.querySelector('textarea') as HTMLTextAreaElement | null;
    expect(textarea).not.toBeNull();
    expect(textarea!.disabled).toBe(false);

    const banner = container!.querySelector('.chat-locked-banner') as HTMLElement | null;
    expect(banner).not.toBeNull();
    const link = banner!.querySelector('a') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    expect(link!.getAttribute('href')).toBe('/onboarding');
  });
});
