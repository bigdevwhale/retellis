'use client';

// Top navigation bar for the public routes. The side rail is for the
// authenticated app; the landing and the guest showcase pages get a horizontal
// bar: brand + the OD 7-tab app nav + theme/lang + an auth CTA (Sign in when
// unauthenticated, Account when signed in).
//
// The 7 OD tabs (Chat / Memory / Journal / Practices / Routing / Personas /
// Family, plus Plans when billing is on) are shown to everyone — guests and
// signed-in users alike. For a guest the tabs open informational showcase
// pages (OD-style pagehead + sample content + a sign-in CTA); for a signed-in
// user they are the real app screens. On narrow viewports the tabs collapse
// into a hamburger sheet (the OD `.nav-mobile` pattern).

import { useAuthCtx } from '@/lib/auth';
import { useLang } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

// Tab icons mirror the OD `stillside-app-130d` nav SVGs (components.css /
// chat.html header) so the landing reads as the same app shell.
const ChatIcon = <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.3A8 8 0 1 1 21 12Z" />;
const HomeIcon = (
  <>
    <path d="M3 11l9-7 9 7" />
    <path d="M5 10v9h14v-9" />
  </>
);
const MemoryIcon = <path d="M4 6h16M4 12h16M4 18h10" />;
const JournalIcon = <path d="M5 4h14v16l-7-3-7 3z" />;
const PracticesIcon = (
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
  </>
);
const RoutingIcon = <path d="M4 7h6l2 2h8v9H4z" />;
const PersonasIcon = (
  <>
    <circle cx="12" cy="8" r="3.2" />
    <path d="M5 20a7 7 0 0 1 14 0" />
  </>
);
const FamilyIcon = (
  <>
    <path d="M9 11a3 3 0 1 1 6 0 3 3 0 0 1-6 0Zm-6 9a6 6 0 0 1 12 0M15 11a3 3 0 0 0 5.5 1.6M16 20a6 6 0 0 0-4-5.7" />
  </>
);
const PlanIcon = (
  <>
    <rect x="3" y="6" width="18" height="13" rx="2" />
    <path d="M3 10h18" />
  </>
);

type Tab = { href: string; key: string; icon: React.ReactNode };

// Primary tabs are the conversation surfaces a user reaches often: Home /
// Chat / Memory / Journal / Practices / Personas. Config surfaces (Routing,
// Family) and Plans are demoted into a "More" dropdown so they aren't peers of
// Chat on the top bar — first-time visitors land on calm companion surfaces,
// not engineering dashboards. Home leads the row so the public landing is
// reachable from any in-app screen; brand click also lands there but a tab is
// the conventional discoverability surface.
const PRIMARY_TABS: Tab[] = [
  { href: '/', key: 'nav.home', icon: HomeIcon },
  { href: '/chat', key: 'nav.chat', icon: ChatIcon },
  { href: '/memory', key: 'nav.memories', icon: MemoryIcon },
  { href: '/journal', key: 'nav.journal', icon: JournalIcon },
  { href: '/practices', key: 'nav.practices', icon: PracticesIcon },
  { href: '/persona', key: 'navtab.personas', icon: PersonasIcon },
];

// Authed users don't see the Home tab — they redirect server-side to /chat.
function filterPrimaryTabs(authed: boolean): Tab[] {
  return authed
    ? PRIMARY_TABS.filter((tab) => tab.href !== '/')
    : PRIMARY_TABS;
}

// Secondary tabs — config + billing surfaces, shown under the "More" dropdown.
const SECONDARY_TABS: Tab[] = [
  { href: '/routing', key: 'navtab.routing', icon: RoutingIcon },
  { href: '/family', key: 'nav.family', icon: FamilyIcon },
];

function TabIcon({ children }: { children: React.ReactNode }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
      {children}
    </svg>
  );
}

export function TopBar() {
  const { t, lang, toggleLang, L2 } = useLang();
  const { toggle: toggleTheme } = useTheme();
  const { principal, config } = useAuthCtx();
  const pathname = usePathname();
  const billing = !!config?.features.billing;
  const authed = Boolean(principal);

  // Mobile hamburger sheet (OD `.nav-mobile` / `.nav-sheet` pattern).
  const [menuOpen, setMenuOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (sheetRef.current && !sheetRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [menuOpen]);

  // Close the "More" dropdown on outside click.
  useEffect(() => {
    if (!moreOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [moreOpen]);

  const isActive = (href: string) =>
    href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`);

  // Secondary tabs (Routing / Family / Plans) live under "More". Plans is
  // appended only when billing is served (hosted instances).
  const secondary: Tab[] = [
    ...SECONDARY_TABS,
    ...(billing ? [{ href: '/plans', key: 'nav.plan', icon: PlanIcon }] : []),
  ];
  // The mobile sheet lists every tab (primary + secondary) flat — no dropdown
  // on the sheet itself.
  const allTabs: Tab[] = [...filterPrimaryTabs(authed), ...secondary];
  const moreActive = secondary.some((tab) => isActive(tab.href));

  return (
    <header className="topbar-nav">
      <Link className="topbar-brand" href="/" aria-label="Retellis home">
        <div className="topbar-mark" aria-hidden>
          ◐
        </div>
        <div className="topbar-brand-text">
          <b>Retellis</b>
          <span>{t('brand.tag')}</span>
        </div>
      </Link>

      <nav className="topbar-links" aria-label="Primary">
        {filterPrimaryTabs(authed).map((tab) => (
          <Link
            key={tab.key}
            href={tab.href}
            aria-current={isActive(tab.href) ? 'page' : undefined}
            className={isActive(tab.href) ? 'active' : ''}
          >
            {tab.icon && <TabIcon>{tab.icon}</TabIcon>}
            <span>{t(tab.key)}</span>
          </Link>
        ))}
        {secondary.length > 0 && (
          <div className="topbar-more" ref={moreRef}>
            <button
              type="button"
              className={`topbar-more-btn${moreActive ? ' active' : ''}`}
              aria-haspopup="menu"
              aria-expanded={moreOpen}
              aria-current={moreActive ? 'page' : undefined}
              onClick={() => setMoreOpen((o) => !o)}
            >
              <span>{t('navtab.more')}</span>
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.6}
              >
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            {moreOpen && (
              <div
                className="topbar-more-menu"
                role="menu"
                style={{ position: 'absolute', top: '100%', right: 0 }}
              >
                {secondary.map((tab) => (
                  <Link
                    key={tab.key}
                    href={tab.href}
                    role="menuitem"
                    aria-current={isActive(tab.href) ? 'page' : undefined}
                    className={isActive(tab.href) ? 'active' : ''}
                    onClick={() => setMoreOpen(false)}
                  >
                    {tab.icon && <TabIcon>{tab.icon}</TabIcon>}
                    <span>{t(tab.key)}</span>
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}
      </nav>

      <div className="topbar-actions">
        <button
          type="button"
          className="icon-mini lang"
          title={lang === 'ru' ? 'Язык' : 'Language'}
          onClick={toggleLang}
        >
          <span className="lbl">{lang.toUpperCase()}</span>
        </button>
        <button type="button" className="icon-mini" title={t('rail.theme')} onClick={toggleTheme}>
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.6}
          >
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
          </svg>
        </button>
        {authed ? (
          <Link className="btn btn-sm" href="/settings">
            {L2({ en: 'Account', ru: 'Аккаунт' })}
          </Link>
        ) : (
          <Link className="btn btn-primary btn-sm" href="/login">
            {L2({ en: 'Sign in', ru: 'Войти' })}
          </Link>
        )}
        {/* Mobile hamburger — the inline tabs hide under 980px (see CSS). */}
        <button
          type="button"
          className="topbar-menubtn"
          aria-label={t('navtab.menu')}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((o) => !o)}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.6}
          >
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
      </div>

      {menuOpen && (
        <div className="topbar-sheet" ref={sheetRef} data-open="true">
          {allTabs.map((tab) => (
            <Link
              key={tab.key}
              href={tab.href}
              aria-current={isActive(tab.href) ? 'page' : undefined}
              className={isActive(tab.href) ? 'active' : ''}
              onClick={() => setMenuOpen(false)}
            >
              {tab.icon && <TabIcon>{tab.icon}</TabIcon>}
              <span>{t(tab.key)}</span>
            </Link>
          ))}
        </div>
      )}
    </header>
  );
}
