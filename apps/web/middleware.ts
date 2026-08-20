// Lightweight cookie-presence gate.
//
// The browser session cookie (`retellis_sess` by default — see
// apps/api config `auth_session_cookie`) is HttpOnly, so this middleware can
// only check *presence*, not validity. Real enforcement is at the API
// (AuthMiddleware resolves the cookie → verified Principal, 401 otherwise).
// Here we only redirect unauthenticated deep links to /login so a fresh browser
// never lands inside the app shell with no session — the API would reject every
// call anyway; this just fails faster and cleaner.
//
// Public paths (no cookie required): the marketing landing (`/`), pricing
// (`/plans`), and the 7 OD feature tabs (`/chat`, `/memory`, `/journal`,
// `/practices`, `/routing`, `/persona`, `/family`). The feature tabs render an
// informational showcase for guests (OD-style pagehead + sample content + a
// sign-in CTA) and the real app screen for signed-in users — so guests browsing
// the landing can open each page from the header nav, the way the OD `.html`
// pages are browsable. Sub-routes (`/family/settings`, `/onboarding`,
// `/settings`, …) are NOT public — they are functional, not informational.
// `/login` is excluded from the matcher entirely. /v1/* is handled by the API /
// Caddy, not Next, so it is excluded from the matcher entirely.

import { type NextRequest, NextResponse } from 'next/server';
import { isPublicRoute } from './lib/public-routes';

const COOKIE = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'retellis_sess';

// Everything except: the login route, Next internals, static, icons, sw, icons,
// manifest, and the API path (proxied elsewhere). Trailing-API /v1 is excluded
// so dev rewrites / prod Caddy routing are never intercepted by this gate.
export const config = {
  matcher: [
    '/((?!login|_next/static|_next/image|favicon.ico|sw.js|manifest.webmanifest|icons|v1).*)',
  ],
};

export function middleware(req: NextRequest) {
  // Public landing/pricing/feature pages render for everyone, cookie or not.
  if (isPublicRoute(req.nextUrl.pathname)) return NextResponse.next();
  if (req.cookies.get(COOKIE)) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = '/login';
  url.searchParams.set('next', req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(url);
}
