// @vitest-environment happy-dom

// Test that the NewChatPicker filters the 'fam' persona out of its
// grid. The 'fam' persona is a family-psychologist surface that
// requires a family vault + a family LLM key — neither of which a
// fresh personal-chat user has. Surfacing it in the personal picker
// was misleading (the user clicked it and got a confusing "no family
// LLM key" banner on /chat). The new entry point is the /family
// page's "Family therapy" CTA, which is owner-aware and gates on the
// actual prerequisites.
//
// We render the picker in isolation (mounting the AppShell would
// require auth + route context) and assert the rendered persona
// list excludes 'fam' even when ``activePersonaId`` was previously
// set to it.

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
  // Open the picker.
  useStore.setState({ newChatPickerOpen: true });
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

describe('NewChatPicker — personal / family split', () => {
  it('does not render the fam persona even when activePersonaId=fam', () => {
    // The user navigated to /chat via /family → "Open family therapy"
    // and then opened the picker. We must not show 'fam' in the
    // grid — re-selecting it from there would be a no-op anyway,
    // and it confuses users who clicked into the picker by accident.
    useStore.setState({ activePersonaId: 'fam' });
    mount();
    const grid = container!.querySelector('.persona-grid');
    expect(grid).not.toBeNull();
    // The exact class on each card is a CSS detail; assert by
    // content: no card's body should match "Family therapist" /
    // "Семейный психолог".
    const allText = grid!.textContent ?? '';
    expect(allText).not.toMatch(/Family therapist|Семейный психолог/);
    // Sanity: at least one personal persona is still rendered
    // (Aria etc.) so we know the picker is actually open and not
    // blank.
    expect(allText).toMatch(/Aria/i);
  });

  it('clicking a personal persona sets activePersonaId and routes to /chat', () => {
    useStore.setState({ activePersonaId: 'aria' });
    mount();
    // Pick the first persona card in the grid.
    const firstCard = container!.querySelector('.persona-grid > *') as HTMLElement;
    expect(firstCard).not.toBeNull();
    act(() => {
      firstCard.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    // The picker closes (closeNewChatPicker) and routes to /chat.
    expect(useStore.getState().newChatPickerOpen).toBe(false);
    expect(push).toHaveBeenCalledWith('/chat');
    // activePersonaId was not 'fam' (we filtered it out) — so the
    // chat screen won't render in family mode. The first card is
    // 'aria' by default.
    const pid = useStore.getState().activePersonaId;
    expect(pid).not.toBe('fam');
  });
});
