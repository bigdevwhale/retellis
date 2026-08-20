'use client';

import { useAuthCtx } from '@/lib/auth';
import { useLang } from '@/lib/i18n';
import { useStore } from '@/lib/store';
import { useTheme } from '@/lib/theme';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { useMemo, useState } from 'react';

type NavItem = {
  screen: string;
  href: string;
  labelKey: string;
  lead: React.ReactNode;
  sub?: { tab?: string; labelKey: string; href: string }[];
  advanced?: boolean;
  // Default `?tab=` value used to highlight a sub-item when the URL carries no
  // tab param (e.g. persona → gallery, practices → breathing).
  defaultTab?: string;
};

const HomeIcon = <path d="M3 11l9-7 9 7M5 10v10h14V10" />;
const ChatIcon = <path d="M21 11.5a8.5 8 0 0 1-12.5 7L4 20l1.5-4.5A8 8 0 1 1 21 11.5z" />;
const CompanionIcon = (
  <>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c1-4 4-6 8-6s7 2 8 6" />
  </>
);
const MemoryIcon = (
  <>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 8v4l3 2" />
  </>
);
// Journal: an open book with a quill stroke — a quiet, read-first diary page.
const JournalIcon = (
  <>
    <path d="M5 4h9a2 2 0 0 1 2 2v14a1 1 0 0 1-1 1H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1z" />
    <path d="M5 4v15" />
    <path d="M15 7l4-2-1.5 5.5L15 13z" />
  </>
);
// Practices: concentric breath circles — a pacer at rest.
const PracticesIcon = (
  <>
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="12" cy="12" r="1.6" />
  </>
);
const PlanIcon = (
  <>
    <rect x="3" y="6" width="18" height="13" rx="2" />
    <path d="M3 10h18" />
  </>
);
const AdvancedIcon = <path d="M4 12h4l2-5 3 10 2-5h5" />;
// Family: two small figures + a roofline — multi-member, the home context.
const FamilyIcon = (
  <>
    <path d="M4 11l8-6 8 6" />
    <path d="M5 10v9h5v-5h4v5h5v-9" />
    <circle cx="9" cy="15" r="1.2" />
    <circle cx="15" cy="15" r="1.2" />
  </>
);
const SettingsIcon = (
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" />
  </>
);

const svg = (children: React.ReactNode, cls = 'lead') => (
  <svg
    aria-hidden="true"
    className={cls}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={1.6}
  >
    {children}
  </svg>
);

const NAV: NavItem[] = [
  { screen: 'home', href: '/', labelKey: 'nav.home', lead: svg(HomeIcon) },
  { screen: 'chat', href: '/chat', labelKey: 'nav.chat', lead: svg(ChatIcon) },
  {
    screen: 'persona',
    href: '/persona',
    labelKey: 'nav.companions',
    lead: svg(CompanionIcon),
    defaultTab: 'gallery',
    sub: [
      { tab: 'gallery', labelKey: 'nav.gallery', href: '/persona?tab=gallery' },
      { tab: 'create', labelKey: 'nav.create', href: '/persona?tab=create' },
    ],
  },
  { screen: 'memory', href: '/memory', labelKey: 'nav.memories', lead: svg(MemoryIcon) },
  { screen: 'family', href: '/family', labelKey: 'nav.family', lead: svg(FamilyIcon) },
  { screen: 'journal', href: '/journal', labelKey: 'nav.journal', lead: svg(JournalIcon) },
  {
    screen: 'practices',
    href: '/practices',
    labelKey: 'nav.practices',
    lead: svg(PracticesIcon),
    defaultTab: 'breathing',
    sub: [
      { tab: 'breathing', labelKey: 'nav.breathing', href: '/practices?tab=breathing' },
      { tab: 'meditation', labelKey: 'nav.meditation', href: '/practices?tab=meditation' },
    ],
  },
  { screen: 'plans', href: '/plans', labelKey: 'nav.plan', lead: svg(PlanIcon) },
  {
    screen: 'routing',
    href: '/routing',
    labelKey: 'nav.advanced',
    lead: svg(AdvancedIcon),
    advanced: true,
    sub: [
      { labelKey: 'nav.routing', href: '/routing' },
      { labelKey: 'nav.keys', href: '/onboarding' },
    ],
  },
  { screen: 'settings', href: '/settings', labelKey: 'nav.settings', lead: svg(SettingsIcon) },
];

export function Rail() {
  const { t } = useLang();
  const { toggle: toggleTheme } = useTheme();
  const { toggleLang, lang } = useLang();
  const { principal, config } = useAuthCtx();
  const pathname = usePathname();
  const search = useSearchParams();
  const tabParam = search.get('tab');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  // Unauthenticated visitors (on the public landing/plans) only see the public
  // nav — Home + Plans. The app routes (chat/persona/memory/journal/practices/
  // advanced/settings) would 401 or redirect to /login, so showing them is
  // misleading. New-chat + the "connected" pill are app features too, so they're
  // hidden as well. Theme + language stay (UI prefs, harmless).
  const authed = Boolean(principal);
  // Plans/billing is a hosted-only capability (features.billing is False on
  // every self-hosted instance). Hide the /plans nav item when the instance
  // can't serve a checkout — don't advertise a capability it doesn't have.
  const billing = !!config?.features.billing;

  const items = useMemo(() => {
    const base = authed ? NAV : NAV.filter((it) => it.screen === 'home' || it.screen === 'plans');
    return base
      .filter((it) => it.screen !== 'plans' || billing)
      .map((it) => {
        const active =
          pathname === it.href || (it.screen !== 'home' && pathname.startsWith(`/${it.screen}`));
        const openByActive = Boolean(active && it.sub);
        return { ...it, active, open: open[it.screen] ?? openByActive };
      });
  }, [pathname, open, authed, billing]);

  const toggleGroup = (screen: string) =>
    setOpen((p) => ({ ...p, [screen]: !(p[screen] ?? false) }));

  return (
    <aside className="rail">
      <div className="rail-top">
        <Link className="brand-btn" href="/" aria-label="Retellis home">
          <div className="mark">◐</div>
          <div className="brand-text">
            <b>Retellis</b>
            <span>{t('brand.tag')}</span>
          </div>
        </Link>
        <button
          type="button"
          className="rail-collapse"
          title={t('rail.collapse')}
          onClick={() => useStore.getState().toggleRail()}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>
      </div>

      {authed && (
        <button
          type="button"
          className="new-chat"
          onClick={() => useStore.getState().openNewChatPicker()}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
          <span>{t('nav.newchat')}</span>
        </button>
      )}

      <nav className="nav">
        {items.map((it) => (
          <div
            key={it.screen}
            className={`nav-group${it.active ? ' active' : ''}${it.open && it.sub ? ' open' : ''}${it.advanced ? ' advanced' : ''}`}
          >
            <Link
              className="ng-head"
              href={it.href}
              onClick={() => it.sub && toggleGroup(it.screen)}
            >
              {it.lead}
              <span>{t(it.labelKey)}</span>
              {it.sub && (
                <svg
                  aria-hidden="true"
                  className="chev"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path d="M9 6l6 6-6 6" />
                </svg>
              )}
            </Link>
            {it.sub && (
              <div className="nav-sub">
                <div>
                  {it.sub.map((s) => {
                    const activeTab = tabParam ?? it.defaultTab ?? null;
                    const subActive =
                      s.tab !== undefined
                        ? it.active && s.tab === activeTab
                        : pathname === new URL(s.href, 'http://x').pathname &&
                          (s.href === it.href ? it.active : pathname === s.href);
                    return (
                      <Link
                        key={s.labelKey + (s.tab ?? '')}
                        className={subActive ? 'active' : ''}
                        href={s.href}
                      >
                        {t(s.labelKey)}
                      </Link>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        ))}
      </nav>

      <div className="rail-foot">
        {authed && (
          <span className="pill-prov">
            <span className="dot" />
            <span className="lbl">{t('rail.connected')}</span>
          </span>
        )}
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
      </div>
    </aside>
  );
}
