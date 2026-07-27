// @vitest-environment happy-dom

// PlansScreen must not advertise paid plans / a checkout CTA on an instance
// that can't serve them. `features.billing` is gated `and is_hosted` server-side
// (apps/api auth/bootstrap.py), so it is False on every self-hosted deployment
// regardless of env. The screen reads the flag from /v1/config and renders an
// honest "not available on this instance" panel instead of the plans grid when
// it is off — "disclose, don't perform".

import { type ReactNode, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PlansScreen } from '../components/screens/PlansScreen';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';

vi.mock('next/navigation', () => ({
  usePathname: () => '/plans',
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

// Mutable config so each test can flip billing without re-declaring the mock.
let billingEnabled = false;
vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => null,
    getAuthConfig: vi.fn(async () => ({
      mode: 'self_hosted',
      profile: 'local',
      auth_backends: ['local'],
      features: {
        billing: billingEnabled,
        credits: false,
        hosted_fallback: false,
        magic_links: false,
        journal: true,
        shares: true,
      },
    })),
  };
});

function tree() {
  return createElement(
    LangProvider,
    null,
    createElement(AuthProvider, null, createElement(PlansScreen) as ReactNode),
  );
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function mount() {
  const c = document.createElement('div');
  container = c;
  document.body.appendChild(c);
  act(() => {
    root = createRoot(c);
    root.render(tree());
  });
}

// PlansScreen returns null while the auth context is loading (config not
// resolved). Poll until boot settles (loading→false) before asserting.
async function settleBoot() {
  for (let i = 0; i < 50; i++) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 5));
    });
  }
}

beforeEach(() => {
  billingEnabled = false;
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  root = null;
  if (container?.parentNode) container.parentNode.removeChild(container);
  container = null;
});

describe('PlansScreen — billing gate', () => {
  it('renders the "not available" panel and no plan cards when billing is off (self-hosted)', async () => {
    billingEnabled = false;
    mount();
    await settleBoot();
    expect(container?.textContent).toContain('Plans are not available on this instance.');
    expect(container?.querySelector('.plan-card')).toBeNull();
  });

  it('renders the plans grid when billing is on (hosted)', async () => {
    billingEnabled = true;
    mount();
    await settleBoot();
    expect(container?.textContent).not.toContain('Plans are not available on this instance.');
    expect(container?.querySelector('.plan-card')).not.toBeNull();
  });
});
