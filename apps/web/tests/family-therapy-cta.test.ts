// @vitest-environment happy-dom

// Test that the FamilySettingsScreen renders the "Family therapy"
// CTA on the primary /family view. With the client-side vault gone,
// the CTA has exactly two states:
//   1. No family provider row → click routes to Settings → Family key.
//   2. Family provider exists → click opens a new family chat.

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FamilySettingsScreen } from '../components/screens/FamilySettingsScreen';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';
import { useStore } from '../lib/store';

const replace = vi.fn();
const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/family',
}));

vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => ({
      user_id: 'u-owner',
      email: 'owner@x.com',
      family_id: 'f-1',
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
    getFamily: vi.fn(async () => ({
      family: {
        id: 'f-1',
        name: 'Test',
        owner_user_id: 'u-owner',
        created_at: '',
        family_salt: null,
        family_enc_blob_seed: null,
      },
      members: [
        {
          user_id: 'u-owner',
          email: 'owner@x.com',
          family_display_name: 'Owner',
          relation: 'self',
          color: '#abc',
          joined_at: '',
        },
      ],
      invites: [],
      providers: [],
    })),
    listFamilyProviders: vi.fn(async () => []),
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
    sealKeyToServer: vi.fn(async () => 'SEALED'),
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  useStore.setState({
    family: {
      id: 'f-1',
      name: 'Test',
      owner_user_id: 'u-owner',
      created_at: '',
      family_salt: null,
      family_enc_blob_seed: null,
      use_owner_personal_key: false,
    },
    familyMembers: [
      {
        family_id: 'f-1',
        user_id: 'u-owner',
        family_role: 'owner',
        family_display_name: 'Owner',
        relation: 'self',
        color: '#abc',
        joined_at: '',
      },
    ],
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
});

function mount() {
  root = createRoot(container!);
  act(() => {
    root!.render(
      createElement(
        AuthProvider,
        null,
        createElement(
          LangProvider,
          null,
          createElement(Suspense, { fallback: null }, createElement(FamilySettingsScreen)),
        ),
      ),
    );
  });
}

describe('FamilySettingsScreen — Family therapy CTA on /family (primary view)', () => {
  it('renders the CTA and the "no family LLM key yet" hint when no provider is set', async () => {
    useStore.setState({ familyProvider: null });
    await act(async () => {
      mount();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const btn = container!.querySelector('[data-family-therapy-cta]') as HTMLButtonElement | null;
    expect(btn).not.toBeNull();
    expect(btn!.disabled).toBe(false);

    const card = btn!.closest('.card');
    expect(card!.textContent).toMatch(/Family key|Семейный ключ/);
  });

  it('routes to /family?tab=settings&subtab=key when the CTA is clicked and no provider exists', async () => {
    useStore.setState({ familyProvider: null });
    await act(async () => {
      mount();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const btn = container!.querySelector('[data-family-therapy-cta]') as HTMLButtonElement | null;
    expect(btn).not.toBeNull();
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    expect(push).toHaveBeenCalledWith('/family?tab=settings&subtab=key');
  });

  it('routes to /chat setting activePersonaId=fam when the CTA is clicked and the family is fully ready', async () => {
    useStore.setState({
      familyProvider: {
        id: 'p-1',
        family_id: 'f-1',
        kind: 'openai',
        label: 'Family',
        base_url: null,
        key_handle: 'kh-1',
        model: 'gpt-4o-mini',
        enc_blob: null,
      },
    });
    await act(async () => {
      mount();
    });
    // The screen's refresh() runs after mount and overwrites the store from
    // the mocked api-client (which returns no providers). Re-apply the
    // provider AFTER the refresh settles, then wait for the next render.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    useStore.setState({
      familyProvider: {
        id: 'p-1',
        family_id: 'f-1',
        kind: 'openai',
        label: 'Family',
        base_url: null,
        key_handle: 'kh-1',
        model: 'gpt-4o-mini',
        enc_blob: null,
      },
    });
    await act(async () => {
      await Promise.resolve();
    });
    const btn = container!.querySelector('[data-family-therapy-cta]') as HTMLButtonElement | null;
    expect(btn).not.toBeNull();
    expect(btn!.disabled).toBe(false);
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });
    expect(useStore.getState().activePersonaId).toBe('fam');
    expect(push).toHaveBeenCalledWith('/chat');
  });
});
