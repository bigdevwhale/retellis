'use client';

// Auth boot + sign-out for the web client.
//
// On mount we read the public /v1/config (deployment mode + enabled backends +
// feature flags) and the verified /v1/auth/me (Principal, or null when no
// session cookie). The login screen renders from config; the rest of the app
// reads `principal` to scope per-user UI. signOut clears the cookie
// server-side; BYOK keys live server-side (envelope-encrypted) and are not
// touched by sign-out — they survive a sign-out and work across devices.

import type { AuthConfig, Principal } from '@ai-companion/contracts';
import { usePathname } from 'next/navigation';
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

import { getAuthConfig, getMe, logout } from './api-client';
import { useStore } from './store';

export type AuthState = {
  config: AuthConfig | null;
  principal: Principal | null;
  loading: boolean; // first boot: config + me in flight
  error: string | null;
  refresh: () => Promise<Principal | null>;
  signOut: () => Promise<void>;
};

const AuthCtx = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const me = await getMe();
    setPrincipal(me);
    return me;
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const cfg = await getAuthConfig();
        if (!alive) return;
        setConfig(cfg);
        const me = await getMe();
        if (!alive) return;
        setPrincipal(me);
        if (me) {
          // Hydrate the family slice from /v1/family. Best-effort: a 404 (no
          // family) clears the slice, other errors are non-fatal. The user's
          // id is passed so the solo picker can default to "me".
          try {
            await useStore.getState().loadFamily(me.user_id);
          } catch {
            /* loadFamily is already best-effort; ignore */
          }
        } else {
          // No principal — clear the family slice so a stale family from a
          // previous session doesn't bleed into the login screen.
          useStore.setState({
            family: null,
            familyMembers: [],
            familyInvites: [],
            familyProvider: null,
            activeFamilyMemberId: null,
          });
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const signOut = useCallback(async () => {
    // Drop the active-provider display pointer (BYOK keys live server-side
    // now and are not touched by sign-out). Best-effort: even if the server
    // call fails, clear the display state locally.
    try {
      await logout();
    } finally {
      setPrincipal(null);
      useStore.getState().setActiveProvider(null);
      // Hard redirect so any cached client state is dropped and middleware
      // re-evaluates the cookie.
      if (typeof window !== 'undefined') {
        window.location.href = '/login';
      }
    }
  }, []);

  return { config, principal, loading, error, refresh, signOut };
}

/** App-wide provider: runs the boot fetch once and exposes it via context. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  return useMemo(
    () => <AuthCtx.Provider value={auth}>{children}</AuthCtx.Provider>,
    [auth, children],
  );
}

export function useAuthCtx(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error('useAuthCtx must be used within AuthProvider');
  return ctx;
}

/**
 * Catches stale cookies the middleware couldn't validate: middleware only
 * checks cookie *presence*, so a revoked/expired session still reaches the app.
 * After /me resolves, if there is no Principal and we're not already on a public
 * page, hard-redirect to /login. Render children optimistically while loading
 * (middleware already confirmed a cookie exists for gated routes).
 *
 * Public pages mirror `lib/public-routes.ts`: the marketing landing (`/`),
 * pricing (`/plans`), and the 7 OD feature tabs — which render informational
 * showcases for guests, so the gate must not bounce them to /login. `/login`
 * itself is added here so the gate never redirects to itself.
 */
const PUBLIC_PAGES = new Set<string>([
  '/',
  '/plans',
  '/login',
  '/chat',
  '/memory',
  '/journal',
  '/practices',
  '/routing',
  '/persona',
  '/family',
]);

export function AuthGate({ children }: { children: ReactNode }) {
  const { principal, loading, error } = useAuthCtx();
  const pathname = usePathname();
  useEffect(() => {
    if (loading) return;
    // I31: only redirect when boot *succeeded* and found no principal. A boot
    // error (network failure or 5xx on /v1/auth/me) leaves ``error`` set and
    // ``principal`` null — but the session may well be valid, so redirecting
    // to /login would log a logged-in user out on every transient server
    // hiccup. Instead, fall through and let the app surface ``error`` (the
    // boot Error message is already exposed on the auth context for screens
    // that want to render it).
    if (error) return;
    if (!principal && !PUBLIC_PAGES.has(pathname) && typeof window !== 'undefined') {
      const next = encodeURIComponent(pathname + window.location.search);
      window.location.href = `/login?next=${next}`;
    }
  }, [loading, error, principal, pathname]);
  return <>{children}</>;
}
