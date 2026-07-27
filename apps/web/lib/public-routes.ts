// Public route classification, shared by the edge middleware (middleware.ts)
// and the client AuthGate (lib/auth.tsx) so the two stay in sync.
//
// LANDING_ROUTES — always public, no per-user data: the marketing landing
// (`/`) and pricing (`/plans`).
//
// FEATURE_ROUTES — the 7 OD app tabs. These are informational/showcase pages
// for guests (OD-style pagehead + sample content + a sign-in CTA) and the real
// authenticated app screens for signed-in users. Guests are NOT redirected to
// /login from these; the page renders a guest showcase instead. Sub-routes
// (e.g. `/family/settings`, `/family/accept`, `/onboarding`, `/settings`)
// are NOT listed here — they stay auth-gated, because they are functional, not
// informational. The match is exact (`has`/`includes`), so `/family` is public
// but `/family/settings` is not.
//
// /login is excluded from the middleware matcher entirely and listed in the
// AuthGate's own public set (it must never redirect to itself).

export const LANDING_ROUTES = ['/', '/plans'] as const;
export const FEATURE_ROUTES = [
  '/chat',
  '/memory',
  '/journal',
  '/practices',
  '/routing',
  '/persona',
  '/family',
] as const;

const LANDING_SET = new Set<string>(LANDING_ROUTES);
const FEATURE_SET = new Set<string>(FEATURE_ROUTES);

/** Landing + feature routes — render for everyone, cookie or not. */
export function isPublicRoute(pathname: string): boolean {
  return LANDING_SET.has(pathname) || FEATURE_SET.has(pathname);
}

/** Only the OD feature tabs (used to pick guest chrome / showcase). */
export function isFeatureRoute(pathname: string): boolean {
  return FEATURE_SET.has(pathname);
}
