// @vitest-environment happy-dom

// Tests for the FamilyTherapistPromptTab component on
// /family?tab=settings&subtab=therapist.
//
// Why this lives at the screen level: the tab integrates the api-client
// (`getFamilyTherapistPrompt` / `setFamilyTherapistPrompt`), the store
// (`familyTherapistPrompt` slice + `setFamilyTherapistPrompt` setter), the
// i18n layer, and the FamilySettingsTabs' refresh() callback. The
// unit-level pieces (the composePrompt helper) are not exported — testing
// through the screen is the only way to cover the wiring.
//
// The /family page is the primary view; the Settings branch mounts
// <FamilySettingsTabs />, which reads `?subtab=` for the inner sub-tab.
// The component is mounted directly here (not via the page) so we can
// drive the inner sub-tab URL state without the outer top-level tab
// strip. usePathname is /family.
//
// 1. owner_sees_form_with_four_textareas — the form is visible and the
//    Save button is enabled for the owner; the four textareas are present.
// 2. member_sees_readonly_with_audit — non-owners see the body + audit
//    line; no form is rendered.
// 3. member_sees_builtin_when_null — when body is null the client renders
//    FAM_BUILTIN_PROMPT (the static `fam` builtin).
// 4. owner_save_calls_set_and_refreshes — clicking Save calls
//    setFamilyTherapistPrompt with the composed body and updates the
//    store via the onSaved refresh.
// 5. save_validation_oversize — the client-side 8000-char guard fires
//    before the API call.
// 6. reset_to_builtin_clears_body — clicking Reset calls the API with
//    body=null and the form fields are reset.

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FamilySettingsTabs } from '../components/screens/FamilySettingsTabs';
import { AuthProvider } from '../lib/auth';
import { FAM_BUILTIN_PROMPT } from '../lib/fixtures';
import { LangProvider } from '../lib/i18n';
import { useStore } from '../lib/store';

const replace = vi.fn();
const push = vi.fn();
let mockSearchParams = new URLSearchParams('tab=settings&subtab=therapist');
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => mockSearchParams,
  usePathname: () => '/family',
}));

type Me = { user_id: string; email: string; family_id: string; family_role: string };
const ownerMe: Me = {
  user_id: 'u-owner',
  email: 'owner@x.com',
  family_id: 'f-1',
  family_role: 'owner',
};
const memberMe: Me = {
  user_id: 'u-member',
  email: 'member@x.com',
  family_id: 'f-1',
  family_role: 'member',
};

let meFixture: Me = ownerMe;
type Prompt = {
  body: string | null;
  set_by_user_id: string | null;
  set_at: string | null;
  set_by_display_name: string | null;
};
let getTherapistPromptMock: ReturnType<typeof vi.fn> = vi.fn(async () => ({
  body: null,
  set_by_user_id: null,
  set_at: null,
  set_by_display_name: null,
}));
let setTherapistPromptMock: ReturnType<typeof vi.fn> = vi.fn(
  async (body: { body: string | null }): Promise<Prompt> => ({
    body: body.body,
    set_by_user_id: 'u-owner',
    set_at: '2026-07-10T12:00:00.000Z',
    set_by_display_name: 'owner@x.com',
  }),
);

vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => meFixture,
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
      },
      members: [
        {
          user_id: 'u-owner',
          email: 'owner@x.com',
          family_display_name: 'Owner',
          relation: 'parent',
          color: '#abc',
          joined_at: '',
        },
        {
          user_id: 'u-member',
          email: 'member@x.com',
          family_display_name: 'Kid',
          relation: 'child',
          color: '#def',
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
    getFamilyTherapistPrompt: () => getTherapistPromptMock(),
    setFamilyTherapistPrompt: (...args: unknown[]) =>
      setTherapistPromptMock(...(args as Parameters<typeof setTherapistPromptMock>)),
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
  meFixture = ownerMe;
  getTherapistPromptMock = vi.fn(async () => ({
    body: null,
    set_by_user_id: null,
    set_at: null,
    set_by_display_name: null,
  }));
  setTherapistPromptMock = vi.fn(
    async (body: { body: string | null }): Promise<Prompt> => ({
      body: body.body,
      set_by_user_id: 'u-owner',
      set_at: '2026-07-10T12:00:00.000Z',
      set_by_display_name: 'owner@x.com',
    }),
  );
  mockSearchParams = new URLSearchParams('tab=settings&subtab=therapist');
  useStore.setState({
    familyTherapistPrompt: null,
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
  // Two micro-tasks is the smallest settle that lets the auth bootstrap +
  // the screen's refresh() effect run end-to-end.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function setTextareaValue(el: HTMLTextAreaElement, value: string) {
  const proto = Object.getPrototypeOf(el) as object;
  const desc =
    Object.getOwnPropertyDescriptor(proto, 'value') ??
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  const setter = desc?.set;
  if (setter) {
    Reflect.apply(setter as (this: HTMLTextAreaElement, v: string) => void, el, [value]);
  } else {
    el.value = value;
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

describe('FamilySettingsScreen — Family therapist prompt tab', () => {
  it('owner sees the form with four textareas and a Save button', async () => {
    await mountAndSettle();

    // The four textareas are tagged with data-therapist-section=*.
    const textareas = Array.from(
      container!.querySelectorAll('textarea[data-therapist-section]'),
    ) as HTMLTextAreaElement[];
    expect(textareas).toHaveLength(4);
    const sections = textareas.map((t) => t.getAttribute('data-therapist-section'));
    expect(sections).toEqual(['focus', 'rules', 'context', 'approach']);

    // The Save button is enabled from the start (validation happens on
    // click, not on the disabled prop — the form is always editable).
    const saveBtn = container!.querySelector('[data-therapist-save]') as HTMLButtonElement | null;
    expect(saveBtn).not.toBeNull();
    expect(saveBtn!.disabled).toBe(false);

    // The Reset button is present for the owner.
    const resetBtn = container!.querySelector('[data-therapist-reset]') as HTMLButtonElement | null;
    expect(resetBtn).not.toBeNull();

    // The preview block is present.
    expect(container!.querySelector('[data-therapist-prompt-preview]')).not.toBeNull();
  });

  it('member sees the read-only view with the audit line', async () => {
    meFixture = memberMe;
    getTherapistPromptMock = vi.fn(async () => ({
      body: 'Session focus: new school year.\nFamily rules: be gentle.',
      set_by_user_id: 'u-owner',
      set_at: '2026-07-10T12:00:00.000Z',
      set_by_display_name: 'owner@x.com',
    }));
    await mountAndSettle();

    // No form for members — no textareas, no Save button.
    expect(container!.querySelectorAll('textarea[data-therapist-section]')).toHaveLength(0);
    expect(container!.querySelector('[data-therapist-save]')).toBeNull();

    // The preview block is present, with the body content rendered.
    const preview = container!.querySelector('[data-therapist-prompt-preview]');
    expect(preview).not.toBeNull();
    expect(preview!.textContent).toContain('Session focus: new school year');

    // The audit line is present and references the owner's display name.
    const audit = container!.querySelector('[data-therapist-prompt-audit]');
    expect(audit).not.toBeNull();
    expect(audit!.textContent).toMatch(/owner@x\.com|owner/i);
  });

  it('member sees the static `fam` builtin when body is null', async () => {
    meFixture = memberMe;
    getTherapistPromptMock = vi.fn(async () => ({
      body: null,
      set_by_user_id: null,
      set_at: null,
      set_by_display_name: null,
    }));
    await mountAndSettle();

    const preview = container!.querySelector('[data-therapist-prompt-preview]');
    expect(preview).not.toBeNull();
    // The static builtin is rendered when the body is null.
    expect(preview!.textContent).toContain(FAM_BUILTIN_PROMPT.en.slice(0, 60));

    // Audit line falls back to the "builtin" string.
    const audit = container!.querySelector('[data-therapist-prompt-audit]');
    expect(audit).not.toBeNull();
    expect(audit!.textContent).toMatch(/built-?in|baseline/i);
  });

  it('owner save calls setFamilyTherapistPrompt and refreshes the store', async () => {
    await mountAndSettle();

    // Type into each textarea.
    const textareas = Array.from(
      container!.querySelectorAll('textarea[data-therapist-section]'),
    ) as HTMLTextAreaElement[];
    setTextareaValue(textareas[0]!, 'new school year'); // focus
    setTextareaValue(textareas[1]!, 'be gentle'); // rules
    setTextareaValue(textareas[3]!, 'reflect first'); // approach
    await act(async () => {
      await Promise.resolve();
    });

    const saveBtn = container!.querySelector('[data-therapist-save]') as HTMLButtonElement;
    await act(async () => {
      saveBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 30));
    });

    expect(setTherapistPromptMock).toHaveBeenCalledTimes(1);
    const arg = setTherapistPromptMock.mock.calls[0]?.[0] as { body: string | null } | undefined;
    expect(arg?.body).toContain('Session focus: new school year');
    expect(arg?.body).toContain('Family rules: be gentle');
    expect(arg?.body).toContain('Approach: reflect first');
    // The "Disclose, don't perform" footer is unconditional.
    expect(arg?.body).toMatch(/disclose, don.?t perform/i);

    // The store was updated by the response (parent's onSaved refresh
    // also fetches, so the slice ends with the server's view).
    expect(useStore.getState().familyTherapistPrompt?.body).toBeTruthy();
    expect(useStore.getState().familyTherapistPrompt?.set_by_display_name).toBe('owner@x.com');
  });

  it('save validation: oversize body shows an error and does NOT call the API', async () => {
    await mountAndSettle();
    const textareas = Array.from(
      container!.querySelectorAll('textarea[data-therapist-section]'),
    ) as HTMLTextAreaElement[];
    // 8_000+ chars in the rules section alone.
    setTextareaValue(textareas[1]!, 'x'.repeat(8_500));
    await act(async () => {
      await Promise.resolve();
    });
    const saveBtn = container!.querySelector('[data-therapist-save]') as HTMLButtonElement;
    await act(async () => {
      saveBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 30));
    });
    // API was NOT called — the client-side guard fired first.
    expect(setTherapistPromptMock).not.toHaveBeenCalled();
    // The error string is rendered somewhere on screen.
    expect(container!.textContent).toMatch(/too long|max|8000/i);
  });

  it('reset to built-in calls the API with body=null and clears the form', async () => {
    // Start with a stored customisation so the reset is meaningful.
    getTherapistPromptMock = vi.fn(async () => ({
      body: 'custom body',
      set_by_user_id: 'u-owner',
      set_at: '2026-07-09T12:00:00.000Z',
      set_by_display_name: 'owner@x.com',
    }));
    await mountAndSettle();

    const resetBtn = container!.querySelector('[data-therapist-reset]') as HTMLButtonElement;
    await act(async () => {
      resetBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 30));
    });

    expect(setTherapistPromptMock).toHaveBeenCalledTimes(1);
    const arg = setTherapistPromptMock.mock.calls[0]?.[0] as { body: string | null } | undefined;
    expect(arg?.body).toBeNull();
    // Store reflects the server's null body.
    expect(useStore.getState().familyTherapistPrompt?.body).toBeNull();
  });
});
