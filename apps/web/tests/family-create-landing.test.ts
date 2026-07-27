// @vitest-environment happy-dom

// Phase 2 #7: a freshly-minted family owner lands on the Settings ->
// Family key sub-tab (with a one-shot "vault ready" flash) instead of
// the Members tab. The family LLM key is the prerequisite for everything
// else the family does, so before this fix the owner had to click
// Settings -> Family key after creating the family to find it.
//
// This test mounts the empty state (no family), drives the create form,
// and asserts the post-create router.replace URL carries
//   tab=settings & subtab=key & flash=vault_ready
// in a single replace (not a push — we don't pollute history).

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FamilySettingsScreen } from '../components/screens/FamilySettingsScreen';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';
import { useStore } from '../lib/store';

const replace = vi.fn();
const push = vi.fn();
let mockSearchParams = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => mockSearchParams,
  usePathname: () => '/family',
}));

// createFamily is the system-under-test. The other api-client methods
// are stubbed so the bootstrap effect doesn't fire real fetches.
let createFamilyMock = vi.fn(async (_body: { name: string }) => ({
  id: 'f-new',
  name: 'My Family',
  owner_user_id: 'u-owner',
  created_at: '',
}));
vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => ({
      user_id: 'u-owner',
      email: 'owner@x.com',
      family_id: null,
      family_role: null,
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
    // No family yet — the empty state renders the create form.
    getFamily: vi.fn(async () => ({ family: null, members: [], invites: [], providers: [] })),
    createFamily: (...args: unknown[]) =>
      createFamilyMock(...(args as Parameters<typeof createFamilyMock>)),
    listFamilyProviders: vi.fn(async () => []),
    getFamilyVaultMeta: vi.fn(async () => ({
      family_id: '',
      vault_initialized: false,
      family_salt: null,
      has_provider: false,
    })),
    listInvites: vi.fn(async () => []),
    getFamilyTherapistPrompt: vi.fn(async () => ({
      body: null,
      set_by_user_id: null,
      set_at: null,
      set_by_display_name: null,
    })),
  };
});

vi.mock('../lib/vault', async () => {
  const actual = await vi.importActual<typeof import('../lib/vault')>('../lib/vault');
  return {
    ...actual,
    hasFamilyVault: async () => false,
    isFamilyVaultUnlocked: () => false,
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  mockSearchParams = new URLSearchParams();
  // Empty state: no family. The create form is the only thing rendered.
  useStore.setState({
    family: null,
    familyMembers: [],
    familyInvites: [],
    familyProvider: null,
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
  createFamilyMock.mockReset();
  createFamilyMock = vi.fn(async (_body: { name: string }) => ({
    id: 'f-new',
    name: 'My Family',
    owner_user_id: 'u-owner',
    created_at: '',
  }));
});

function tree() {
  return createElement(
    AuthProvider,
    null,
    createElement(
      LangProvider,
      null,
      createElement(Suspense, { fallback: null }, createElement(FamilySettingsScreen)),
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

function setInputValue(el: HTMLInputElement, value: string) {
  const proto = Object.getPrototypeOf(el) as object;
  const desc =
    Object.getOwnPropertyDescriptor(proto, 'value') ??
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  const setter = desc?.set;
  if (setter) {
    Reflect.apply(setter as (this: HTMLInputElement, v: string) => void, el, [value]);
  } else {
    el.value = value;
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

describe('FamilySettingsScreen — owner lands on the Family key tab after create', () => {
  it('after createFamily, router.replace targets /family?tab=settings&subtab=key&flash=vault_ready', async () => {
    await mountAndSettle();

    // The empty-state create form: a name input + a Create button.
    const nameInput = container!.querySelector(
      'input[aria-label="family name"]',
    ) as HTMLInputElement | null;
    expect(nameInput).not.toBeNull();
    const createBtn = Array.from(container!.querySelectorAll('button')).find(
      (b) => (b.textContent ?? '').trim() === 'Create',
    ) as HTMLButtonElement | undefined;
    expect(createBtn).toBeTruthy();

    setInputValue(nameInput!, 'My Family');
    await act(async () => {
      createBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 20));
    });

    // createFamily was called with the trimmed name.
    expect(createFamilyMock).toHaveBeenCalledTimes(1);
    expect(createFamilyMock.mock.calls[0]?.[0]).toEqual({ name: 'My Family' });

    // The owner is routed straight to the Family key sub-tab with the
    // "vault ready" flash — NOT pushed (no history pollution), and NOT
    // left on the Members tab.
    expect(replace).toHaveBeenCalledWith('/family?tab=settings&subtab=key&flash=vault_ready');
    expect(push).not.toHaveBeenCalled();
  });
});
