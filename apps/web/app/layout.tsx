import type { Metadata, Viewport } from 'next';
import { cookies } from 'next/headers';
import './globals.css';
import { AppShell } from '@/components/AppShell';
import { SwRegister } from '@/components/SwRegister';
import { Toaster } from '@/components/ui/Toaster';
import { AuthGate, AuthProvider } from '@/lib/auth';
import { LangProvider } from '@/lib/i18n';
import { ThemeProvider } from '@/lib/theme';

export const metadata: Metadata = {
  title: 'Retellis — calm AI, your keys or ours',
  description:
    'Retellis — an open-source AI companion for calm, clarity, and inner peace. Bring your own keys, or let us handle it.',
  manifest: '/manifest.webmanifest',
  applicationName: 'Retellis',
  icons: {
    icon: [{ url: '/icons/icon.svg', type: 'image/svg+xml' }],
    apple: [{ url: '/icons/icon.svg' }],
  },
  appleWebApp: { capable: true, statusBarStyle: 'black-translucent', title: 'Retellis' },
};

export const viewport: Viewport = {
  // Media-split so the browser picks the correct status-bar tint pre-hydration
  // based on system preference — otherwise light-theme users see a dark status
  // bar on first paint. ThemeProvider still overrides this post-mount when the
  // user has an explicit saved choice.
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fbfaf7' },
    { media: '(prefers-color-scheme: dark)', color: '#0d253d' },
  ],
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

// Pre-hydration script: reads companion.theme / companion.lang from
// localStorage and applies them to <html> SYNCHRONOUSLY, before React
// hydrates. Without this the static HTML ships with ``data-theme="dark"``
// + ``lang="en"`` and the client providers flip to the saved values
// inside a useEffect — producing a visible dark→light (or light→dark)
// flash on every page load and a perceptible jump on every navigation
// that re-mounts the providers.
//
// The two providers' initial state is now read from the live DOM (via
// ``document.documentElement.dataset.theme`` / ``.lang``) instead of
// defaulting to 'dark' / 'en' — so the first React render matches the
// markup the user actually saw. Any localStorage write goes through
// the providers as before.
//
// Language defaulting: a saved ``companion.lang`` preference always wins.
// With no saved preference, the boot script sniffs the browser language
// (``navigator.languages[0]`` / ``navigator.language``) — ru* → 'ru',
// anything else → 'en'. LangProvider picks up ``document.documentElement
// .lang`` as its initial state, so the UI opens in the browser language
// on first visit; the first effect tick persists that choice to
// ``companion.lang`` so a later browser-language change does not override
// an explicit UI toggle.
//
// Because the boot script mutates <html> attributes before hydration,
// React will still log a server/client mismatch; ``suppressHydrationWarning``
// on <html> silences that expected, intentional difference.
const themeBoot = `(function(){try{var t=localStorage.getItem('companion.theme');if(t==='dark'||t==='light'){document.documentElement.setAttribute('data-theme',t);}var l=localStorage.getItem('companion.lang');if(l==='en'||l==='ru'){document.documentElement.lang=l;}else{var nl=(navigator.languages&&navigator.languages[0])||navigator.language||'';document.documentElement.lang=(nl.toLowerCase().indexOf('ru')===0)?'ru':'en';}}catch(e){}})();`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Read the session cookie presence server-side so AppShell can pick the
  // chrome (TopBar for guests on feature routes, Rail for authed) without a
  // client flash — the cookie is HttpOnly so the client can't read it, but the
  // server can. This mirrors the middleware's cookie-presence gate; a stale
  // cookie still routes to the Rail optimistically and AuthGate handles the
  // redirect to /login. `cookies()` is async in Next 15 — await it.
  const cookieStore = await cookies();
  const cookieName = process.env.NEXT_PUBLIC_SESSION_COOKIE ?? 'retellis_sess';
  const hasSession = Boolean(cookieStore.get(cookieName));
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: the
            inline boot script reads only localStorage keys we wrote
            ourselves and applies them as ``data-theme`` / ``lang``
            attributes — there is no user-controlled content and no
            XSS surface. */}
        <script dangerouslySetInnerHTML={{ __html: themeBoot }} />
      </head>
      <body>
        <ThemeProvider>
          <LangProvider>
            <AuthProvider>
              <AuthGate>
                <AppShell hasSession={hasSession}>{children}</AppShell>
              </AuthGate>
              <Toaster />
              <SwRegister />
            </AuthProvider>
          </LangProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
