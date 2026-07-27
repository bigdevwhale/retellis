// @vitest-environment happy-dom

// SettingsScreen — the Billing tab (hosted-only, gated by features.billing).
//
// The tab renders the subscription status from GET /v1/billing/subscription
// (the single source of truth — the checkout callback redirect does NOT
// mutate state), a "Manage subscription" button that hits
// POST /v1/billing/portal and redirects, and a past_due / canceled banner
// when the subscription is in those states. When there's no subscription
// (free tier) it shows a "View plans" link instead.

import { Suspense, act, createElement } from 'react';
import { type Root, createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsScreen } from '../components/screens/SettingsScreen';
import { AuthProvider } from '../lib/auth';
import { LangProvider } from '../lib/i18n';
import { ThemeProvider } from '../lib/theme';

const replace = vi.fn();
const push = vi.fn();
let mockSearchParams = new URLSearchParams('tab=billing');
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push, back: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => mockSearchParams,
  usePathname: () => '/settings',
}));

// Per-test overrides for the billing endpoints. ``vi.hoisted`` makes the mock
// fn available to the hoisted vi.mock factory (the factory runs before any
// top-level const is initialized).
const mocks = vi.hoisted(() => ({
  createPortalSession: vi.fn(async () => ({ redirect_url: 'https://portal.example/x' })),
}));
let subscriptionValue: import('@ai-companion/contracts').Subscription | null = null;

vi.mock('../lib/api-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/api-client')>('../lib/api-client');
  return {
    ...actual,
    getMe: async () => ({
      user_id: 'u-1',
      subject: 'u-1',
      issuer: 'local',
      email: 'u@x.com',
      display_name: 'User',
      plan: 'plus_ww',
      credits_usd: 10,
      auth_backend: 'local',
      family_id: null,
    }),
    getAuthConfig: vi.fn(async () => ({
      mode: 'hosted',
      profile: 'hosted',
      auth_backends: ['local'],
      features: {
        billing: true,
        credits: true,
        hosted_fallback: false,
        magic_links: false,
        journal: true,
        shares: true,
      },
    })),
    getSubscription: vi.fn(async () => subscriptionValue),
    createPortalSession: mocks.createPortalSession,
    listProviders: vi.fn(async () => []),
    listSessions: vi.fn(async () => []),
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
  mockSearchParams = new URLSearchParams('tab=billing');
  subscriptionValue = null;
  mocks.createPortalSession.mockClear();
  // jsdom/happy-dom: stub the redirect target of the portal handler.
  // window.location.href is read-only in jsdom; assign via Object.defineProperty.
  Object.defineProperty(window, 'location', {
    value: { href: '' },
    writable: true,
    configurable: true,
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
        ThemeProvider,
        null,
        createElement(
          AuthProvider,
          null,
          createElement(
            LangProvider,
            null,
            createElement(Suspense, { fallback: null }, createElement(SettingsScreen)),
          ),
        ),
      ),
    );
  });
  // Let the subscription + boot promises flush.
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 5));
    });
  }
}

function billingTab(): HTMLButtonElement | undefined {
  return Array.from(container!.querySelectorAll('button[data-settings-tab]')).find(
    (b) => b.getAttribute('data-settings-tab') === 'billing',
  ) as HTMLButtonElement | undefined;
}

describe('SettingsScreen — billing tab', () => {
  it('shows the Billing tab only when features.billing is on', async () => {
    await mountAndSettle();
    expect(billingTab()).toBeTruthy();
  });

  it('renders the free-tier state with a View plans link when there is no subscription', async () => {
    subscriptionValue = null;
    await mountAndSettle();
    expect(container!.textContent).toMatch(/Free plan|Бесплатный тариф/);
    const link = container!.querySelector('a[href="/plans"]');
    expect(link).not.toBeNull();
  });

  it('renders the subscription status + Manage button when a subscription exists', async () => {
    subscriptionValue = {
      id: 's-1',
      user_id: 'u-1',
      plan_slug: 'plus_ww',
      provider: 'paddle',
      provider_sub_id: 'ps-1',
      status: 'active',
      current_period_start: '2026-07-01T00:00:00Z',
      current_period_end: '2026-08-01T00:00:00Z',
      cancel_at_period_end: false,
      trial_ends_at: null,
      billing_country: 'WW',
      created_at: '2026-07-01T00:00:00Z',
    };
    await mountAndSettle();
    const planTag = container!.querySelector('[data-billing-plan]');
    expect(planTag?.textContent).toBe('plus_ww');
    const statusChip = container!.querySelector('[data-billing-status]');
    expect(statusChip?.textContent).toBe('active');
    const manageBtn = Array.from(container!.querySelectorAll('button')).find((b) =>
      /Manage subscription|Управлять подпиской/.test(b.textContent ?? ''),
    );
    expect(manageBtn).toBeTruthy();
  });

  it('renders a past_due banner when the subscription is past_due', async () => {
    subscriptionValue = {
      id: 's-2',
      user_id: 'u-1',
      plan_slug: 'pro_ww',
      provider: 'paddle',
      provider_sub_id: 'ps-2',
      status: 'past_due',
      current_period_start: '2026-07-01T00:00:00Z',
      current_period_end: '2026-08-01T00:00:00Z',
      cancel_at_period_end: false,
      trial_ends_at: null,
      billing_country: 'WW',
      created_at: '2026-07-01T00:00:00Z',
    };
    await mountAndSettle();
    expect(container!.querySelector('[data-billing-banner="past_due"]')).not.toBeNull();
  });

  it('redirects to the provider portal when Manage subscription is clicked', async () => {
    subscriptionValue = {
      id: 's-3',
      user_id: 'u-1',
      plan_slug: 'plus_ww',
      provider: 'paddle',
      provider_sub_id: 'ps-3',
      status: 'active',
      current_period_start: '2026-07-01T00:00:00Z',
      current_period_end: '2026-08-01T00:00:00Z',
      cancel_at_period_end: false,
      trial_ends_at: null,
      billing_country: 'WW',
      created_at: '2026-07-01T00:00:00Z',
    };
    await mountAndSettle();
    const manageBtn = Array.from(container!.querySelectorAll('button')).find((b) =>
      /Manage subscription|Управлять подпиской/.test(b.textContent ?? ''),
    ) as HTMLButtonElement;
    expect(manageBtn).toBeTruthy();
    await act(async () => {
      manageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 5));
    });
    expect(mocks.createPortalSession).toHaveBeenCalled();
    expect(window.location.href).toBe('https://portal.example/x');
  });
});
