'use client';

import { isFeatureRoute } from '@/lib/public-routes';
import { useStore } from '@/lib/store';
import { usePathname } from 'next/navigation';
import { Suspense } from 'react';
import { EmailVerifyBanner } from './EmailVerifyBanner';
import { NewChatPicker } from './NewChatPicker';
import { Rail } from './Rail';
import { TopBar } from './TopBar';

export function AppShell({
  children,
  hasSession,
}: {
  children: React.ReactNode;
  // Server-read cookie presence (see app/layout.tsx). Decides the chrome for
  // feature routes without a client flash: a signed-in user (cookie present)
  // gets the Rail app shell; a guest (no cookie) gets the landing TopBar so the
  // header nav stays consistent across the showcase pages. Stale cookies route
  // to the Rail optimistically and AuthGate handles the redirect to /login.
  hasSession: boolean;
}) {
  const collapsed = useStore((s) => s.railCollapsed);
  const pathname = usePathname();

  // /login is a standalone screen — no rail, no top bar, and NOT inside the
  // `.app` grid (which always reserves a 248px rail column and would shove the
  // form into the corner). The auth gate + cookie-presence middleware keep
  // unauthenticated users here, so the app chrome would be meaningless (and
  // every nav link would 401 anyway).
  if (pathname === '/login') {
    return <div className="login-shell">{children}</div>;
  }

  // Public landing routes (`/`, `/plans`) get a horizontal top bar instead of
  // the side rail — a marketing page, not the app shell.
  if (pathname === '/' || pathname === '/plans') {
    return (
      <div className="landing-shell">
        <TopBar />
        <Suspense fallback={null}>
          <EmailVerifyBanner />
        </Suspense>
        <main>
          <div className="screen active">{children}</div>
        </main>
      </div>
    );
  }

  // Feature routes (`/chat`, `/memory`, …) are informational showcase pages for
  // guests and the real app screens for signed-in users. Guests get the landing
  // TopBar (header nav) + the showcase; signed-in users get the Rail app shell.
  // `hasSession` is determined server-side from cookie presence, so there is no
  // Rail↔TopBar flash on first paint.
  if (isFeatureRoute(pathname) && !hasSession) {
    return (
      <div className="landing-shell">
        <TopBar />
        <main>
          <div className="screen active">{children}</div>
        </main>
      </div>
    );
  }

  return (
    <div className={`app${collapsed ? ' collapsed' : ''}`}>
      <Suspense fallback={<div className="rail rail-skel" aria-hidden="true" />}>
        <Rail />
      </Suspense>
      <main>
        <Suspense fallback={null}>
          <EmailVerifyBanner />
        </Suspense>
        <div className="screen active">{children}</div>
      </main>
      <NewChatPicker />
    </div>
  );
}
