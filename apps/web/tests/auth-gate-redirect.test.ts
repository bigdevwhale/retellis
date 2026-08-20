// @vitest-environment happy-dom

// I31: the AuthGate must redirect to /login ONLY when boot resolved and found
// no Principal (401 → null). A 5xx or network failure on /v1/auth/me during
// boot leaves the auth context in an *error* state with principal=null —
// the session may well be valid, so redirecting would log a logged-in user
// out on every transient server hiccup. The gate now skips the redirect when
// `error` is set and lets the app surface it instead.
//
// We drive AuthProvider (which runs the real boot effect calling getMe) with
// a mocked api-client, and capture `window.location.href` assignments to
// assert the redirect decision.

import { type ReactNode, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthGate, AuthProvider } from '../lib/auth';

function tree(child: ReactNode) {
  return createElement(AuthProvider, null, createElement(AuthGate, null, child));
}

const NAVIGATIONS: string[] = [];

vi.mock('next/navigation', () => ({
  // /settings is a protected route (not in PUBLIC_PAGES / FEATURE_ROUTES), so
  // AuthGate's 401 → /login redirect still applies there. The feature routes
  // (/chat, /memory, …) are now public showcase pages for guests and are
  // deliberately NOT redirected — see lib/public-routes.ts + GuestFeature.
  usePathname: () => '/settings',
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), refresh: vi.fn() }),
}));

// Capture `window.location.href =` assignments without actually navigating.
// happy-dom would otherwise tear the document down on a real navigation. The
// `href` is a getter/setter pair backed by a closure variable — TS forbids a
// data property and an accessor with the same name in one object literal, so
// we don't declare a `href` data field alongside `set href`.
let originalLocationDescriptor: PropertyDescriptor | undefined;
let locationHref = '';
beforeEach(() => {
  NAVIGATIONS.length = 0;
  locationHref = '';
  originalLocationDescriptor = Object.getOwnPropertyDescriptor(window, 'location');
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      get href() {
        return locationHref;
      },
      set href(v: string) {
        NAVIGATIONS.push(v);
        locationHref = v;
      },
      get search() {
        return '';
      },
      get pathname() {
        return '/settings';
      },
      toString() {
        return 'http://localhost/settings';
      },
    },
  });
});
afterEach(() => {
  if (originalLocationDescriptor) {
    Object.defineProperty(window, 'location', originalLocationDescriptor);
  }
});

let getMeImpl: () => Promise<unknown> = async () => null;
let getAuthConfigImpl: () => Promise<unknown> = async () => ({
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
});

vi.mock('../lib/api-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api-client')>();
  return {
    ...actual,
    getMe: () => getMeImpl(),
    getAuthConfig: () => getAuthConfigImpl(),
    logout: vi.fn(async () => undefined),
  };
});

// loadFamily (called from the boot effect when a principal exists) hits the
// family API; stub the store helper so it no-ops rather than fetching.
vi.mock('../lib/store', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/store')>();
  return {
    ...actual,
    useStore: {
      ...actual.useStore,
      getState: () => ({ ...actual.useStore.getState(), loadFamily: async () => undefined }),
      setState: actual.useStore.setState,
    },
  };
});

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function mount(node: ReactNode) {
  const c = document.createElement('div');
  container = c;
  document.body.appendChild(c);
  act(() => {
    root = createRoot(c);
    root.render(node);
  });
}

// Wait for the boot effect (getAuthConfig + getMe) to settle. The auth context
// flips loading→false in a `finally`; we poll until it's idle.
async function settleBoot() {
  for (let i = 0; i < 50; i++) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 5));
    });
  }
}

beforeEach(() => {
  getMeImpl = async () => null;
  getAuthConfigImpl = async () => ({
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

describe('AuthGate — redirect on 401 only, not on 5xx (I31)', () => {
  it('redirects to /login when boot resolves with no Principal (401 → null)', async () => {
    getMeImpl = async () => null;
    mount(tree('app'));
    await settleBoot();
    expect(NAVIGATIONS.some((u) => u.startsWith('/login'))).toBe(true);
  });

  it('does NOT redirect when /v1/auth/me fails with a 5xx (boot error)', async () => {
    getMeImpl = async () => {
      throw new Error('/v1/auth/me → 500');
    };
    mount(tree('app'));
    await settleBoot();
    // The session may be valid — the gate must NOT kick to /login.
    expect(NAVIGATIONS.some((u) => u.startsWith('/login'))).toBe(false);
  });

  it('does NOT redirect when a Principal is present (happy path)', async () => {
    getMeImpl = async () => ({ user_id: 'u1', display_name: 'A' });
    mount(tree('app'));
    await settleBoot();
    expect(NAVIGATIONS.length).toBe(0);
  });
});
