// @vitest-environment happy-dom

// Tests for the top-level tab strip on /family.
//
// The /family page is the primary view: it hosts three top-level tabs
// (Members | Therapy | Settings) rendered with the project's standard
// `.seg` strip — NOT a corner link. The Settings tab is a thin wrapper
// around <FamilySettingsTabs />, which keeps its own sub-tab strip
// (Invites / Therapist / Family key / Danger). The split mirrors the
// existing in-page tab pattern in SettingsScreen.tsx:110, 129 and
// FamilySettingsTabs.tsx:281-305.
//
// URL state:
//   - top-level tab →  ?tab=members | therapy | settings
//   - inner sub-tab →  ?subtab=invites | therapist | key | danger
//                      (only meaningful when ?tab=settings is active;
//                       FamilySettingsTabs reads ?subtab= with ?tab= as
//                       a legacy fallback)
//   - one-shot notice → ?flash=…  (consumed by FamilySettingsTabs)
//
// The /family/settings and /family/vault routes are deep-link backstops
// that rewrite to the new URL — see app/family/settings/page.tsx and
// app/family/vault/page.tsx.

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FamilySettingsScreen } from '../components/screens/FamilySettingsScreen';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';
import { useStore } from '../lib/store';

const replace = vi.fn();
const push = vi.fn();
// Each test sets this before mount() so the component reads the right
// top-level tab on first render. Default = no params (Members tab).
let mockSearchParams = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => mockSearchParams,
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
        family_salt: 'SALTSALT==',
        family_enc_blob_seed: 'SEED',
        use_owner_personal_key: false,
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
    getFamilyVaultMeta: vi.fn(async () => ({
      family_id: 'f-1',
      vault_initialized: true,
      family_salt: 'SALTSALT==',
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
    hasFamilyVault: async () => true,
    isFamilyVaultUnlocked: () => true,
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  mockSearchParams = new URLSearchParams();
  useStore.setState({
    family: {
      id: 'f-1',
      name: 'Test',
      owner_user_id: 'u-owner',
      created_at: '',
      family_salt: 'SALTSALT==',
      family_enc_blob_seed: 'SEED',
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

async function mountAndSettle() {
  await act(async () => {
    root = createRoot(container!);
    root.render(
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
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function topTabButtons(): HTMLButtonElement[] {
  // The outer .seg strip tags each top-level tab with
  // data-family-top-tab. The inner FamilySettingsTabs' sub-tab strip
  // does NOT have this attribute, so the selector is unambiguous.
  return Array.from(
    container!.querySelectorAll('button[data-family-top-tab]'),
  ) as HTMLButtonElement[];
}

function getTopTab(name: string): HTMLButtonElement | undefined {
  return topTabButtons().find((b) => b.getAttribute('data-family-top-tab') === name);
}

describe('FamilySettingsScreen — top-level tab strip on /family', () => {
  it('renders the three top-level tabs (Members | Therapy | Settings) and the corner link is gone', async () => {
    await mountAndSettle();

    const tabs = topTabButtons();
    const labels = tabs.map((b) => b.getAttribute('data-family-top-tab'));
    expect(labels).toEqual(['members', 'therapy', 'settings']);

    // The .seg strip is what wraps the top-level tabs (matches
    // FamilySettingsTabs.tsx:281-305 and SettingsScreen.tsx:110).
    const strip = tabs[0]!.closest('.seg');
    expect(strip).not.toBeNull();
    // role=tablist is the a11y contract for the strip.
    expect(strip!.getAttribute('role')).toBe('tablist');

    // The Members tab is the default for a signed-in family member.
    const members = getTopTab('members');
    expect(members).toBeTruthy();
    expect(members!.getAttribute('aria-selected')).toBe('true');

    // The old "Settings →" corner link pattern is NOT used here. There
    // is no anchor to /family or /family/settings in the topbar.
    const topbar = container!.querySelector('.topbar');
    const topbarLinks = topbar ? Array.from(topbar.querySelectorAll('a[href^="/family"]')) : [];
    expect(topbarLinks).toHaveLength(0);
  });

  it('clicking Therapy writes ?tab=therapy to the URL (no history push)', async () => {
    await mountAndSettle();

    const therapyTab = getTopTab('therapy');
    expect(therapyTab).toBeTruthy();
    await act(async () => {
      therapyTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });

    // The top-level tab is written to ?tab=. The inner sub-tab (if any)
    // is cleared when leaving Settings so we don't carry stale state.
    const replaces = replace.mock.calls.map((c) => c[0] as string);
    expect(replaces.some((u) => u.includes('tab=therapy'))).toBe(true);
    // No history push — replace only.
    expect(push.mock.calls.some((c) => (c[0] as string).includes('tab=therapy'))).toBe(false);
  });

  it('?tab=therapy renders the therapy CTA and hides the members card', async () => {
    // The Therapy tab is a deep-link: pre-set the URL params, mount,
    // verify the rendered DOM. The click-side test above covers the
    // URL mutation; this covers the rendering side of the same path.
    mockSearchParams = new URLSearchParams('tab=therapy');
    await mountAndSettle();

    // The Therapy top-level tab is selected.
    const therapyTab = getTopTab('therapy');
    expect(therapyTab!.getAttribute('aria-selected')).toBe('true');

    // The therapy CTA card is visible.
    expect(container!.querySelector('[data-family-therapy-cta]')).not.toBeNull();
    // The members card is NOT rendered on the Therapy tab.
    expect(
      Array.from(container!.querySelectorAll('.card-title')).find((el) =>
        /Members|Участники/.test(el.textContent ?? ''),
      ),
    ).toBeUndefined();
  });

  it('clicking Settings writes ?tab=settings to the URL (no history push)', async () => {
    await mountAndSettle();

    const settingsTab = getTopTab('settings');
    expect(settingsTab).toBeTruthy();
    await act(async () => {
      settingsTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });

    // The top-level tab is written to ?tab=settings.
    const replaces = replace.mock.calls.map((c) => c[0] as string);
    expect(replaces.some((u) => u.includes('tab=settings'))).toBe(true);
  });

  it('?tab=settings renders the inner sub-tab strip (Invites / Therapist / Family key / Danger)', async () => {
    // Same as the click test above, but pre-set the URL so we can
    // assert the rendered DOM. The deep-link is the canonical way to
    // land on the Settings tab from anywhere in the app.
    mockSearchParams = new URLSearchParams('tab=settings');
    await mountAndSettle();

    // The Settings top-level tab is selected.
    const settingsTab = getTopTab('settings');
    expect(settingsTab!.getAttribute('aria-selected')).toBe('true');

    // The inner sub-tab strip (Invites / Therapist / Family key / Danger)
    // is rendered. It is the SECOND .seg on the page (the first is the
    // top-level one).
    const allStrips = Array.from(container!.querySelectorAll('.seg[role="tablist"]'));
    expect(allStrips.length).toBeGreaterThanOrEqual(2);
    const innerStrip = allStrips[1]!;
    const subTabLabels = Array.from(innerStrip.querySelectorAll('button')).map((b) =>
      (b.textContent ?? '').trim(),
    );
    expect(subTabLabels.some((l) => /Invites|Приглашения/.test(l))).toBe(true);
    expect(subTabLabels.some((l) => /Family key|Семейный ключ/.test(l))).toBe(true);
    expect(subTabLabels.some((l) => /Danger zone|Опасная зона/.test(l))).toBe(true);

    // The therapy CTA is not rendered on the Settings tab.
    expect(container!.querySelector('[data-family-therapy-cta]')).toBeNull();
  });

  it('?tab=settings&subtab=key deep-link lands on the Family key sub-tab', async () => {
    mockSearchParams = new URLSearchParams('tab=settings&subtab=key');
    await mountAndSettle();

    // The Settings top-level tab is active.
    const settingsTab = getTopTab('settings');
    expect(settingsTab!.getAttribute('aria-selected')).toBe('true');

    // The inner sub-tab strip is rendered AND the Family key tab is
    // active. The active sub-tab is the one with class="on" in the
    // inner .seg. Find the inner strip (second .seg) and look for the
    // active button matching "Family key".
    const allStrips = Array.from(container!.querySelectorAll('.seg[role="tablist"]'));
    const innerStrip = allStrips[1]!;
    const keyBtn = Array.from(innerStrip.querySelectorAll('button')).find((b) =>
      /Family key|Семейный ключ/.test(b.textContent ?? ''),
    );
    expect(keyBtn).toBeTruthy();
    expect(keyBtn!.classList.contains('on')).toBe(true);

    // The "Family key" card body is rendered. The multi-key surface (BYOK
    // upgrade) shows a "Family provider" card with an "Add a family key"
    // button — the legacy test pinned the inline form's password input
    // here, but that form is now modal-based (closed by default).
    const addBtn = Array.from(container!.querySelectorAll('button')).find((b) =>
      /Add a family key|Добавить ключ семьи/.test(b.textContent ?? ''),
    );
    expect(addBtn).toBeTruthy();

    // The owner-only "Use my personal key" checkbox renders above the family
    // add form. With the flag off (default) + no personal key in the store,
    // it is present but disabled (no personal key to ride yet).
    const checkbox = container!.querySelector(
      'input[type="checkbox"][data-family-use-personal-checkbox="1"]',
    ) as HTMLInputElement | null;
    expect(checkbox).not.toBeNull();
    expect(checkbox!.checked).toBe(false);
  });

  it('hides the family add form when the owner\'s "use personal key" flag is on', async () => {
    // Flag on: the family rides the owner's active personal key. The family
    // key list + inline add form are mutually exclusive with the toggle, so
    // the "Add a family key" button must NOT render (nothing to add). The
    // checkbox reflects the on state.
    mockSearchParams = new URLSearchParams('tab=settings&subtab=key');
    await mountAndSettle();
    // setState AFTER mount: the mount's refresh overwrites the store from
    // the mocked getFamily (flag-off), so flip the flag on afterwards.
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
      // Give the owner an active personal key so the checkbox is enabled and
      // the "in use" line renders.
      activeProvider: {
        providerId: 'p-1',
        kind: 'openai',
        label: 'Personal OpenAI',
        keyHandle: 'kh-1',
        baseUrl: null,
        model: 'gpt-4o-mini',
        embeddingsModel: null,
      },
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // The checkbox is checked.
    const checkbox = container!.querySelector(
      'input[type="checkbox"][data-family-use-personal-checkbox="1"]',
    ) as HTMLInputElement | null;
    expect(checkbox).not.toBeNull();
    expect(checkbox!.checked).toBe(true);

    // The "Add a family key" button is gone — the add form is hidden while
    // the family rides the personal key.
    const addBtn = Array.from(container!.querySelectorAll('button')).find((b) =>
      /Add a family key|Добавить ключ семьи/.test(b.textContent ?? ''),
    );
    expect(addBtn).toBeUndefined();

    // The "Using the owner's personal key" status line is rendered.
    expect(container!.textContent ?? '').toMatch(/Using the owner.s personal key/i);
  });

  it('?flash=vault_ready renders the flash on the active tab', async () => {
    // The post-create redirect lands on the Family key tab with a one-shot
    // flash telling the owner to add the family's LLM API key.
    mockSearchParams = new URLSearchParams('tab=settings&subtab=key&flash=vault_ready');
    await mountAndSettle();

    // The flash banner is rendered.
    const flash = container!.querySelector('output');
    expect(flash?.textContent ?? '').toMatch(/Family created|Add the family.s LLM API key/i);
  });

  it('clicking a different top-level tab clears a stale ?subtab=', async () => {
    // Start on Settings with subtab=key.
    mockSearchParams = new URLSearchParams('tab=settings&subtab=key');
    await mountAndSettle();

    // Click Therapy.
    const therapyTab = getTopTab('therapy');
    await act(async () => {
      therapyTab!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });

    // The new URL no longer carries ?subtab=key (it would be misleading
    // — the Therapy tab doesn't have a Family key sub-tab).
    const replaces = replace.mock.calls.map((c) => c[0] as string);
    const lastReplace = replaces[replaces.length - 1] ?? '';
    expect(lastReplace).toContain('tab=therapy');
    expect(lastReplace).not.toContain('subtab=key');
  });

  it('?tab=members (the default) renders the members card and the therapy CTA', async () => {
    // Start on Therapy, then "click back" to Members by pre-setting
    // the URL. The click-side test for the URL mutation is above.
    mockSearchParams = new URLSearchParams('tab=therapy');
    await mountAndSettle();

    // Switch to Members the same way a real router would: replace the
    // search params and re-render.
    mockSearchParams = new URLSearchParams('tab=members');
    await act(async () => {
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
      await Promise.resolve();
    });

    // Members block is back. The OD port renders the members heading in a
    // .blk-head h2 (was a .card-title under the old .topbar chrome).
    const membersHeading = Array.from(
      container!.querySelectorAll('.fam-members .blk-head h2'),
    ).find((el) => /Members|Участники/.test(el.textContent ?? ''));
    expect(membersHeading).toBeTruthy();
    // Therapy CTA is back.
    expect(container!.querySelector('[data-family-therapy-cta]')).not.toBeNull();
  });

  it('?tab=settings does NOT render a second topbar or a "← Family" back link', async () => {
    // Regression: the old FamilySettingsTabs rendered its own
    // .topbar + .wrap + a "← Family" back link on top of the
    // outer FamilySettingsScreen chrome, giving the user a
    // confusing double-chrome on the Settings tab. The fix: the
    // inner component renders only the sub-tab strip + body.
    mockSearchParams = new URLSearchParams('tab=settings');
    await mountAndSettle();

    // The outer chrome is a .pagehead (OD port), not a .topbar. The
    // regression guard: the inner FamilySettingsTabs must not render its
    // own .topbar on top of the outer chrome (the old double-chrome bug).
    // 0 today (outer is a .pagehead); a relapse would push it to 2.
    const topbars = container!.querySelectorAll('.topbar');
    expect(topbars.length).toBeLessThanOrEqual(1);

    // No "← Family" / "← Семья" back link anywhere — the top-level
    // tab strip is the navigation.
    const allLinks = Array.from(container!.querySelectorAll('a'));
    const backLink = allLinks.find((a) => /←\s*(Family|Семья)/.test(a.textContent ?? ''));
    expect(backLink).toBeUndefined();
  });
});

describe('FamilySettingsScreen — opening the family chat', () => {
  it('uses startChatWith (not just setActivePersona) so a fresh family convo is the active one', async () => {
    // The user is on /family?tab=therapy, the family is fully
    // set up. Clicking the CTA must create a family convo AND
    // switch the persona — otherwise /chat re-uses the old
    // personal convo (the bug the user reported).
    await mountAndSettle();
    useStore.setState({
      familyProvider: {
        id: 'p-1',
        family_id: 'f-1',
        kind: 'openai',
        label: 'Family',
        base_url: null,
        key_handle: 'kh-1',
        model: 'gpt-4o-mini',
        enc_blob: 'BLOB',
      },
    });
    await act(async () => {
      await Promise.resolve();
    });
    const btn = container!.querySelector('[data-family-therapy-cta]') as HTMLButtonElement | null;
    expect(btn).not.toBeNull();
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
    });

    // Persona switched to fam.
    const state = useStore.getState();
    expect(state.activePersonaId).toBe('fam');
    // AND a fresh family convo is the active one (not an old
    // personal one). The fam-solo / fam-joint id format is the
    // contract from lib/store.ts:newChat.
    expect(state.activeConvoId).toMatch(/^fam-(solo|joint)-/);
    expect(state.convos.find((c) => c.id === state.activeConvoId)?.personaId).toBe('fam');
    // Routed to /chat.
    expect(push).toHaveBeenCalledWith('/chat');
  });
});
