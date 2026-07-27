// @vitest-environment happy-dom

// Phase 2 #8: a non-owner on the Family key tab (with no family provider
// yet) used to see a dead-greyed provider form — every field disabled,
// no explanation. The fix replaces it with a plain "Only the family
// owner can add or change the family key — ask them." notice. Non-owners
// can read the existing-provider card (when one exists) but can never
// add/change a key, so a disabled form's inputs are noise.
//
// This test mounts FamilySettingsTabs directly (as
// family-provider-form.test.ts does) with a NON-owner principal and a
// family whose owner is someone else, navigates to the Family key
// sub-tab, and asserts:
//   1. the non-owner notice text is rendered;
//   2. the API-key input is NOT rendered (no dead-greyed form);
//   3. the "Go to unlock" anchor (Phase 2 #6, owner-only) is NOT
//      rendered — that notice is the owner-locked-vault path, which a
//      non-owner never sees.

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FamilySettingsTabs } from '../components/screens/FamilySettingsTabs';
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

// Non-owner principal: a member of the family, NOT the owner.
const memberMe = {
  user_id: 'u-member',
  email: 'member@x.com',
  family_id: 'f-1',
  family_role: 'member',
};

vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => memberMe,
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
        owner_user_id: 'u-owner', // someone ELSE owns the family
        created_at: '',
        family_salt: 'SALTSALT==',
        family_enc_blob_seed: 'SEED',
        use_owner_personal_key: false,
      },
      members: [
        {
          user_id: 'u-member',
          email: 'member@x.com',
          family_display_name: 'Member',
          relation: 'self',
          color: '#abc',
          joined_at: '',
        },
      ],
      invites: [],
      providers: [],
    })),
    listInvites: vi.fn(async () => []),
    listFamilyProviders: vi.fn(async () => []),
    getFamilyVaultMeta: vi.fn(async () => ({
      family_id: 'f-1',
      vault_initialized: true,
      family_salt: 'SALTSALT==',
      has_provider: false,
    })),
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
    hasFamilyVault: async () => true,
    isFamilyVaultUnlocked: () => true,
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  mockSearchParams = new URLSearchParams('tab=settings&subtab=key');
  useStore.setState({
    family: {
      id: 'f-1',
      name: 'Test',
      owner_user_id: 'u-owner', // member is NOT the owner
      created_at: '',
      family_salt: 'SALTSALT==',
      family_enc_blob_seed: 'SEED',
      use_owner_personal_key: false,
    },
    familyMembers: [
      {
        family_id: 'f-1',
        user_id: 'u-member',
        family_role: 'member',
        family_display_name: 'Member',
        relation: 'self',
        color: '#abc',
        joined_at: '',
      },
    ],
    familyInvites: [],
    familyProvider: null, // no provider yet -> the form/notice branch
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
      createElement(Suspense, { fallback: null }, createElement(FamilySettingsTabs)),
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

describe('FamilySettingsTabs — non-owner on the Family key tab', () => {
  it('renders the "ask the owner" notice and NOT the API-key input (no dead-greyed form)', async () => {
    await mountAndSettle();

    // The non-owner notice is rendered (localized en/ru — the test
    // harness defaults to en via LangProvider).
    const bodyText = container!.textContent ?? '';
    expect(bodyText).toMatch(/Only the family owner can add or change the family key/);

    // The API-key input is NOT rendered — the dead-greyed form is gone.
    const apiKeyInput = container!.querySelector('input[type="password"][placeholder="API key"]');
    expect(apiKeyInput).toBeNull();

    // The owner-only "locked vault" notice (Phase 2 #6) is NOT rendered
    // for a non-owner — that path is gated on isOwner upstream.
    expect(container!.querySelector('[data-family-provider-locked-notice]')).toBeNull();
  });

  it('renders the "owner personal key" notice when the family uses the owner’s personal key', async () => {
    // Flag on: the family rides the owner's active personal key. A non-owner
    // can't change this; they see a distinct notice (ask the owner for a
    // different model) instead of the default "ask the owner to add a key".
    // setState runs AFTER mount so the mount's own refresh (which overwrites
    // the store from the mocked getFamily) has already settled.
    await mountAndSettle();
    useStore.setState({
      family: {
        id: 'f-1',
        name: 'Test',
        owner_user_id: 'u-owner',
        created_at: '',
        family_salt: 'SALTSALT==',
        family_enc_blob_seed: 'SEED',
        use_owner_personal_key: true,
      },
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const bodyText = container!.textContent ?? '';
    expect(bodyText).toMatch(/The family uses the owner.s personal LLM key/i);

    // The default flag-off notice must NOT also leak through.
    expect(bodyText).not.toMatch(/Only the family owner can add or change the family key/);

    // No owner-only checkbox for a non-owner.
    expect(container!.querySelector('[data-family-use-personal-checkbox="1"]')).toBeNull();
  });
});
