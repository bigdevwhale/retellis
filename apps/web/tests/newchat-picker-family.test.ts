// @vitest-environment happy-dom

// Phase 3 #17: the New Chat picker has a "Family therapy" shortcut below
// the personal grid. It links to /family?tab=therapy — it does NOT call
// ``startChatWith('fam')`` — so the /family setup gate still applies (the
// family vault + key must exist before a family turn can run). ``fam``
// stays filtered out of the personal grid (see picker-filter.test.ts).
//
// This test asserts:
//   1. the "Family therapy" link targets /family?tab=therapy;
//   2. clicking it does NOT start a fam chat — activePersonaId is not set
//      to 'fam' and the router is not pushed to '/chat';
//   3. the picker closes on click (so the overlay doesn't linger over
//      /family).

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewChatPicker } from '../components/NewChatPicker';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';
import { useStore } from '../lib/store';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat',
}));

let container: HTMLDivElement | null = null;
let root: Root | null = null;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  useStore.setState({ newChatPickerOpen: true, activePersonaId: 'aria' });
  push.mockReset();
});

afterEach(() => {
  if (root) {
    act(() => {
      root!.unmount();
    });
    root = null;
  }
  if (container) {
    container.remove();
    container = null;
  }
  useStore.setState({ newChatPickerOpen: false });
});

function mount() {
  root = createRoot(container!);
  act(() => {
    root!.render(
      createElement(
        Suspense,
        { fallback: null },
        createElement(
          AuthProvider,
          null,
          createElement(LangProvider, null, createElement(NewChatPicker)),
        ),
      ),
    );
  });
}

describe('NewChatPicker — Family therapy shortcut', () => {
  it('renders a "Family therapy" link targeting /family?tab=therapy', async () => {
    mount();
    await act(async () => {
      await Promise.resolve();
    });
    const link = container!.querySelector('[data-family-therapy-pick]') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
    // The link's href targets the family page's Therapy tab — the setup
    // gate lives there, NOT on /chat.
    expect(link!.getAttribute('href')).toContain('/family');
    expect(link!.getAttribute('href')).toContain('tab=therapy');
    // The label is the localized "Open family therapy →" copy.
    expect(link!.textContent ?? '').toMatch(/family therapy|семейн/i);
  });

  it('clicking the Family therapy link does NOT start a fam chat (no startChatWith, no /chat push)', async () => {
    mount();
    await act(async () => {
      await Promise.resolve();
    });
    const link = container!.querySelector('[data-family-therapy-pick]') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();

    // Capture the persona before the click so we can prove it didn't
    // flip to 'fam' (the old, wrong behavior would have started a fam
    // chat directly).
    const before = useStore.getState().activePersonaId;

    await act(async () => {
      link!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });

    // activePersonaId did NOT flip to 'fam' — the family entry is a
    // navigation affordance, not a persona pick.
    expect(useStore.getState().activePersonaId).toBe(before);
    expect(useStore.getState().activePersonaId).not.toBe('fam');

    // The picker closes (the link's onClick calls closeNewChatPicker).
    expect(useStore.getState().newChatPickerOpen).toBe(false);

    // The router was NOT pushed to /chat (the family entry routes
    // through /family, not the chat screen). next/link may or may not
    // call the mocked push depending on the test renderer; the key
    // negative assertion is "not /chat".
    const pushedTo = push.mock.calls.map((c) => c[0] as string);
    expect(pushedTo.some((u) => u === '/chat')).toBe(false);
  });
});
