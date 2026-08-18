'use client';

// Settings — Account / Sessions / Appearance / Key vault / Your data.
//
// The five sections are presented as a click-driven tab strip (the same
// `.seg role="tablist"` pattern FamilySettingsTabs uses on /family), NOT a
// scroll-spy. A scroll-spy was tried first but it never highlighted Account
// at the top or Key vault / Your data at the bottom — the IntersectionObserver
// band can't reach the first/last sections — so tabs are used instead: the
// active section is always unambiguous and deep-linkable via `?tab=`. Only the
// active panel renders.

import { AddProviderModal } from '@/components/byok/AddProviderModal';
import type { ProviderKeyFormValues } from '@/components/byok/ProviderKeyForm';
import { ProviderKeyList } from '@/components/byok/ProviderKeyList';
import { IntegrationsSection } from '@/components/integrations/IntegrationsSection';
import {
  type ProviderRecord,
  type SessionInfoRecord,
  createPortalSession,
  createProvider,
  deleteProvider,
  getHealth,
  getSubscription,
  listProviders,
  listSessions,
  revokeOtherSessions,
  revokeSession,
  updateProvider,
} from '@/lib/api-client';
import { useAuthCtx } from '@/lib/auth';
import { useLang } from '@/lib/i18n';
import { type ProviderKind, providerMeta } from '@/lib/providerCatalog';
import { useStore } from '@/lib/store';
import { useTheme } from '@/lib/theme';
import { type KeyPayload, newKeyHandle, sealKeyToServer } from '@/lib/vault';
import type { Subscription } from '@ai-companion/contracts';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState } from 'react';

// Human-readable name for a provider kind. Built from the catalog (single
// source of truth) at module load — no fixture duplication. The fallback
// keeps the UI safe if the server ever returns a stale kind string.
const KIND_NAME: Record<ProviderKind, string> = {
  openai: providerMeta('openai').label,
  anthropic: providerMeta('anthropic').label,
  google: providerMeta('google').label,
  openrouter: providerMeta('openrouter').label,
  ollama: providerMeta('ollama').label,
  azure: providerMeta('azure').label,
  aihubmix: providerMeta('aihubmix').label,
  bedrock: providerMeta('bedrock').label,
};

type Tab = 'account' | 'sessions' | 'billing' | 'appearance' | 'vault' | 'integrations' | 'data';

const VALID_TABS = new Set<Tab>([
  'account',
  'sessions',
  'billing',
  'appearance',
  'vault',
  'integrations',
  'data',
]);

// Sessions surface: infer a device class from the user-agent so each row can
// carry a small desktop / mobile / tablet glyph. Falls back to desktop when
// the UA is missing (the "This device" row has no UA from the server).
type DeviceKind = 'desktop' | 'mobile' | 'tablet';
function deviceKind(ua: string | null | undefined): DeviceKind {
  if (!ua) return 'desktop';
  const l = ua.toLowerCase();
  if (/(ipad|tablet|playbook|silk)/.test(l)) return 'tablet';
  if (/(iphone|android|mobile|phone|webos|blackberry)/.test(l)) return 'mobile';
  return 'desktop';
}

// A tiny OS · browser caption parsed from the UA. Intentionally coarse — the
// exact string is already shown (truncated) as the row label; this is just a
// quieter secondary line so the user can scan "which device" at a glance.
function deviceCaption(
  ua: string | null | undefined,
  L2: (s: { en: string; ru: string }) => string,
): string {
  if (!ua) return L2({ en: 'This device', ru: 'Это устройство' });
  const l = ua.toLowerCase();
  const os = /(iphone|ipad|ios)/.test(l)
    ? 'iOS'
    : /(macintosh|mac os|macos)/.test(l)
      ? 'macOS'
      : /(windows)/.test(l)
        ? 'Windows'
        : /(android)/.test(l)
          ? 'Android'
          : /(linux)/.test(l)
            ? 'Linux'
            : null;
  const browser = /(edg|edge)/.test(l)
    ? 'Edge'
    : /(chrome|crios)/.test(l)
      ? 'Chrome'
      : /(firefox|fxios)/.test(l)
        ? 'Firefox'
        : /(safari)/.test(l)
          ? 'Safari'
          : null;
  return [os, browser].filter(Boolean).join(' · ') || L2({ en: 'Unknown', ru: 'Неизвестно' });
}

function DeviceGlyph({ kind }: { kind: DeviceKind }) {
  if (kind === 'mobile') {
    return (
      <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <rect
          x="4.5"
          y="1.5"
          width="7"
          height="13"
          rx="1.5"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <path d="M7 12.5h2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      </svg>
    );
  }
  // tablet & desktop share the monitor glyph; tablet would differ only slightly
  // and the brand favors restraint over a third icon for a rare case.
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect
        x="1.5"
        y="2.5"
        width="13"
        height="8.5"
        rx="1.5"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path d="M5.5 13.5h5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

// Suspense boundary so useSearchParams can suspend (Next.js 14+ requires it
// for any client component that reads searchParams).
export function SettingsScreen() {
  return (
    <Suspense fallback={null}>
      <SettingsScreenInner />
    </Suspense>
  );
}

function SettingsScreenInner() {
  const { t, lang, setLang, L2 } = useLang();
  const { theme, setTheme } = useTheme();
  const { principal, signOut, config } = useAuthCtx();
  const router = useRouter();
  const searchParams = useSearchParams();

  const activeProvider = useStore((s) => s.activeProvider);
  const setActiveProvider = useStore((s) => s.setActiveProvider);

  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [loaded, setLoaded] = useState(false);
  // M2: active-session management. The session token is never surfaced — only
  // the opaque surrogate id (see lib/api-client listSessions). ``current`` is
  // the session whose cookie is on this browser; it cannot be revoked here.
  const [sessions, setSessions] = useState<SessionInfoRecord[]>([]);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);
  // Multi-key BYOK affordance: an "Add a key" button on the empty state and
  // an "Add another key" button on the rows list both open this modal. The
  // modal returns the new key; the handler stores it in the local vault and
  // POSTs a ProviderRecord. The new key does NOT become active — the user
  // promotes it via the per-row "Set as active" button or the chat-side
  // model switcher.
  const [addOpen, setAddOpen] = useState(false);
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  // Billing tab — the user's current subscription (null = free tier). Loaded
  // only when billing is a hosted capability. The checkout callback redirect
  // does NOT mutate state; webhooks are the single source of truth, so this is
  // re-read on tab open rather than trusted from the redirect.
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [subLoaded, setSubLoaded] = useState(false);
  const [portalBusy, setPortalBusy] = useState(false);
  const [portalErr, setPortalErr] = useState<string | null>(null);

  // Active tab: read from `?tab=` so the section is deep-linkable (matches
  // FamilySettingsTabs' `?subtab=` pattern). Sessions is only a valid landing
  // when authenticated; Billing is only a valid landing when the instance
  // sells plans. Clamp to Account otherwise so a stale deep link never lands
  // on a panel that won't render. The billing clamp is deferred until /v1/config
  // resolves: ``billingOn`` is false while config is loading, so clamping
  // eagerly would drop a ``?tab=billing`` deep link to Account before we even
  // know whether the instance sells plans. The stale-tab effect below
  // corrects it once config loads and billing turns out to be off.
  const billingOn = !!config?.features.billing;
  const configLoaded = !!config;
  const initialTab = ((): Tab => {
    const q = searchParams.get('tab');
    if (q && VALID_TABS.has(q as Tab)) {
      if (q === 'sessions' && !principal) return 'account';
      if (q === 'billing' && configLoaded && !billingOn) return 'account';
      return q as Tab;
    }
    return 'account';
  })();
  const [tab, setTabState] = useState<Tab>(initialTab);

  // Self-correct a stale Billing deep link once /v1/config resolves and the
  // instance turns out not to sell plans (initialTab can't, per above).
  useEffect(() => {
    if (tab === 'billing' && configLoaded && !billingOn) setTabState('account');
  }, [tab, configLoaded, billingOn]);

  const setTab = useCallback(
    (next: Tab) => {
      setTabState(next);
      const sp = new URLSearchParams(searchParams.toString());
      sp.set('tab', next);
      router.replace(`/settings?${sp.toString()}`);
    },
    [router, searchParams],
  );

  const refresh = async () => {
    setProviders(await listProviders());
  };

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions());
      setSessionsError(null);
    } catch (e) {
      setSessionsError(e instanceof Error ? e.message : String(e));
    } finally {
      setSessionsLoaded(true);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      const remote = await listProviders();
      if (!alive) return;
      setProviders(remote);
      setLoaded(true);
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Load the session list only when authenticated (the Sessions tab is hidden
  // otherwise).
  useEffect(() => {
    if (!principal) return;
    let alive = true;
    (async () => {
      await refreshSessions();
      alive = false;
    })();
    return () => {
      alive = false;
    };
  }, [principal, refreshSessions]);

  // Load the subscription when the Billing tab is opened (and billing is on).
  // Re-read on each open so a checkout-return or webhook-driven change is
  // reflected without a full reload.
  useEffect(() => {
    if (!billingOn || tab !== 'billing') return;
    let alive = true;
    (async () => {
      try {
        setSubscription(await getSubscription());
      } catch {
        // Non-fatal — the panel renders the free-tier state on a fetch failure
        // rather than blocking the tab.
      } finally {
        if (alive) setSubLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [billingOn, tab]);

  const managePortal = async () => {
    setPortalErr(null);
    setPortalBusy(true);
    try {
      const session = await createPortalSession();
      // Redirect to the provider's self-service portal (cancel, change card,
      // invoices). Managed by the provider — we never build our own cancel/card
      // UI. A 503 (provider unconfigured) surfaces as the error banner.
      window.location.href = session.redirect_url;
    } catch (e) {
      setPortalErr(e instanceof Error ? e.message : String(e));
      setPortalBusy(false);
    }
  };

  const remove = async (id: string) => {
    await deleteProvider(id);
    if (activeProvider?.providerId === id) setActiveProvider(null);
    await refresh();
  };

  const revokeOne = async (id: string) => {
    try {
      await revokeSession(id);
      await refreshSessions();
    } catch (e) {
      setSessionsError(e instanceof Error ? e.message : String(e));
    }
  };

  const revokeOthers = async () => {
    try {
      await revokeOtherSessions();
      await refreshSessions();
    } catch (e) {
      setSessionsError(e instanceof Error ? e.message : String(e));
    }
  };

  // Add a key: ECDH-seal the plaintext key to the server's session pubkey
  // (one-time, at onboarding or here in Settings) and create a new
  // ProviderRecord. The server envelope-encrypts the key at rest under its
  // DEK. The new key does NOT become active — the user promotes it via the
  // per-row "Set as active" or the chat-side switcher.
  const addKey = async (values: ProviderKeyFormValues) => {
    setAddBusy(true);
    setAddError(null);
    try {
      const h = await getHealth();
      const payload: KeyPayload = {
        provider_kind: values.kind,
        api_key: values.apiKey,
        base_url: values.baseUrl ?? null,
        extra: values.extra ?? null,
      };
      const sealed = await sealKeyToServer(JSON.stringify(payload), h.ecdh_pub);
      const handle = newKeyHandle();
      const rec = await createProvider({
        kind: values.kind,
        label: values.label,
        key_handle: handle,
        base_url: values.baseUrl,
        model: values.model || null,
        embeddings_model: values.embeddingsModel,
        enc_blob: null,
        enc_key_blob: sealed,
      });
      setProviders((prev) => [...prev, rec]);
      setAddOpen(false);
    } catch (e) {
      setAddError(e instanceof Error ? e.message : String(e));
    } finally {
      setAddBusy(false);
    }
  };

  // Promote a key to active. The store keeps a single `activeProvider`
  // pointer; promoting a new one transparently routes the next turn to it.
  // The key stays server-side; no client-side re-decrypt needed.
  const setActive = async (id: string) => {
    const p = providers.find((x) => x.id === id);
    if (!p) return;
    setActiveProvider({
      providerId: p.id,
      kind: p.kind,
      label: p.label,
      keyHandle: p.key_handle ?? '',
      baseUrl: p.base_url,
      model: p.model,
      embeddingsModel: p.embeddings_model,
    });
  };

  const creditsEnabled = !!config?.features.credits;
  const otherSessions = sessions.filter((s) => !s.current);

  // Account hero avatar initials — display name's first letters, else subject.
  const avatarInitials = (() => {
    const name = principal?.display_name ?? principal?.subject ?? '';
    return (
      name
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((w) => w[0]?.toUpperCase() ?? '')
        .join('') || '?'
    );
  })();

  const tabs: { id: Tab; label: string }[] = [
    { id: 'account', label: L2({ en: 'Account', ru: 'Аккаунт' }) },
    ...(principal ? [{ id: 'sessions' as Tab, label: L2({ en: 'Sessions', ru: 'Сессии' }) }] : []),
    ...(billingOn ? [{ id: 'billing' as Tab, label: L2({ en: 'Billing', ru: 'Оплата' }) }] : []),
    { id: 'appearance', label: t('set.appearance') },
    { id: 'vault', label: t('set.vault') },
    { id: 'integrations', label: L2({ en: 'Integrations', ru: 'Интеграции' }) },
    { id: 'data', label: t('set.data') },
  ];

  return (
    <>
      <div className="topbar">
        <h2>{t('set.title')}</h2>
        <span className="sub">{t('set.sub')}</span>
      </div>
      <div className="wrap">
        {/* Tab strip — same .seg role="tablist" pattern as /family. Clicking
            switches the panel and writes `?tab=` (replace, no history push). */}
        <div
          className="seg set-tabs"
          role="tablist"
          style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 20 }}
        >
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              data-settings-tab={item.id}
              className={tab === item.id ? 'on' : ''}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="set-content">
          {/* 1 · Account (hero) */}
          {tab === 'account' && (
            <section className="card set-hero" aria-labelledby="account-h">
              <div className="hero-top">
                <span className="set-avatar" aria-hidden="true">
                  {avatarInitials}
                </span>
                <div className="hero-id">
                  <h3 className="hero-name" id="account-h">
                    {principal
                      ? (principal.display_name ?? principal.email ?? principal.subject)
                      : ''}
                  </h3>
                  {principal ? (
                    <div className="set-meta">
                      {principal.email && principal.display_name && (
                        <span className="chip">{principal.email}</span>
                      )}
                      <span className="tag" title={L2({ en: 'Plan', ru: 'Тариф' })}>
                        {principal.plan}
                      </span>
                      {creditsEnabled && (
                        <span
                          className="chip set-credits"
                          title={L2({
                            en: 'Hosted credits remaining',
                            ru: 'Остаток хостинг-кредитов',
                          })}
                        >
                          <span
                            className="dot"
                            style={{ background: 'var(--mint)' }}
                            aria-hidden="true"
                          />
                          {L2({ en: 'credits', ru: 'кредиты' })}: $
                          {principal.credits_usd.toFixed(2)}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="set-meta">
                      <span className="help" style={{ margin: 0 }}>
                        {L2({ en: 'Not signed in.', ru: 'Вход не выполнен.' })}
                      </span>
                    </div>
                  )}
                </div>
                {principal && (
                  <div className="hero-action">
                    <button
                      type="button"
                      className="btn btn-danger-ghost"
                      onClick={() => void signOut()}
                    >
                      {L2({ en: 'Sign out', ru: 'Выйти' })}
                    </button>
                  </div>
                )}
              </div>
              {principal && (
                <p className="help">
                  {L2({
                    en: `Signed in via ${principal.auth_backend}. Signing out clears keys from this device.`,
                    ru: `Вход через ${principal.auth_backend}. Выход очищает ключи с этого устройства.`,
                  })}
                </p>
              )}
            </section>
          )}

          {/* 2 · Sessions (only when authenticated) */}
          {tab === 'sessions' && principal && (
            <section className="card" aria-labelledby="sessions-h">
              <h3 className="card-title" id="sessions-h">
                {L2({ en: 'Sessions', ru: 'Сессии' })}
              </h3>
              <p className="card-desc">
                {L2({
                  en: 'Active devices signed in to this account. Revoke any you don’t recognize.',
                  ru: 'Устройства, вошедшие в этот аккаунт. Отзывайте те, что вам незнакомы.',
                })}
              </p>

              {!sessionsLoaded ? (
                <div className="help" style={{ margin: 0 }}>
                  {L2({ en: 'Loading…', ru: 'Загрузка…' })}
                </div>
              ) : sessions.length === 0 ? (
                <div className="help" style={{ margin: 0 }}>
                  {L2({ en: 'No active sessions.', ru: 'Нет активных сессий.' })}
                </div>
              ) : (
                <div className="set-rows">
                  {sessions.map((s) => {
                    const kind = deviceKind(s.user_agent);
                    return (
                      <div className="set-row" key={s.id}>
                        <span className="set-glyph" aria-hidden="true">
                          <DeviceGlyph kind={kind} />
                        </span>
                        <div className="set-row-main">
                          <span className="set-row-label">
                            <span className="ua">
                              {s.user_agent
                                ? s.user_agent.length > 48
                                  ? `${s.user_agent.slice(0, 48)}…`
                                  : s.user_agent
                                : L2({ en: 'This device', ru: 'Это устройство' })}
                            </span>
                            {s.current && (
                              <>
                                <span
                                  className="dot"
                                  style={{ background: 'var(--mint)' }}
                                  aria-hidden="true"
                                />
                                <span className="current-tag">
                                  {L2({ en: '· current', ru: '· текущая' })}
                                </span>
                              </>
                            )}
                          </span>
                          <span className="set-row-cap">{deviceCaption(s.user_agent, L2)}</span>
                        </div>
                        <span className="until">
                          {L2({ en: 'until', ru: 'до' })} {new Date(s.expires_at).toLocaleString()}
                        </span>
                        {s.current ? (
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost"
                            onClick={() => void signOut()}
                            title={L2({
                              en: 'Use Sign out to end the current session',
                              ru: 'Используйте «Выйти» для завершения текущей сессии',
                            })}
                          >
                            {L2({ en: 'Sign out', ru: 'Выйти' })}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost"
                            onClick={() => revokeOne(s.id)}
                          >
                            {L2({ en: 'Revoke', ru: 'Отозвать' })}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {sessionsError && (
                <div className="help" style={{ marginTop: 8, color: 'var(--danger, #c33)' }}>
                  {sessionsError}
                </div>
              )}
              {otherSessions.length > 0 && (
                <div className="set-foot">
                  <button type="button" className="btn btn-ghost" onClick={() => revokeOthers()}>
                    {L2({
                      en: 'Sign out all other sessions',
                      ru: 'Выйти на всех остальных устройствах',
                    })}
                  </button>
                </div>
              )}
            </section>
          )}

          {/* 2b · Billing (only when the instance sells plans) */}
          {tab === 'billing' && billingOn && (
            <section className="card" aria-labelledby="billing-h">
              <h3 className="card-title" id="billing-h">
                {L2({ en: 'Billing', ru: 'Оплата' })}
              </h3>
              <p className="card-desc">
                {L2({
                  en: 'Your subscription and payment method are managed by the provider. Cancel or change your card any time — your memory export stays yours.',
                  ru: 'Подписка и способ оплаты управляются провайдером. Отменить или сменить карту можно в любой момент — экспорт памяти остаётся у вас.',
                })}
              </p>

              {!subLoaded ? (
                <div className="help" style={{ margin: 0 }}>
                  {L2({ en: 'Loading…', ru: 'Загрузка…' })}
                </div>
              ) : subscription ? (
                <>
                  {subscription.status === 'past_due' && (
                    <div
                      className="set-banner set-banner--warn"
                      data-billing-banner="past_due"
                      style={{ marginBottom: 12 }}
                    >
                      <span className="set-banner-line">
                        <span
                          className="dot"
                          style={{ background: 'var(--amber, #e6a700)' }}
                          aria-hidden="true"
                        />
                        {L2({
                          en: 'A recent payment failed. Update your card to keep your plan.',
                          ru: 'Последний платёж не прошёл. Обновите карту, чтобы сохранить тариф.',
                        })}
                      </span>
                    </div>
                  )}
                  {subscription.status === 'canceled' && (
                    <div
                      className="set-banner set-banner--warn"
                      data-billing-banner="canceled"
                      style={{ marginBottom: 12 }}
                    >
                      <span className="set-banner-line">
                        <span
                          className="dot"
                          style={{ background: 'var(--danger, #c0392b)' }}
                          aria-hidden="true"
                        />
                        {L2({
                          en: 'Your subscription is canceled. It runs until the period ends, then reverts to free.',
                          ru: 'Подписка отменена. Она действует до конца периода, затем вернётся на бесплатный тариф.',
                        })}
                      </span>
                    </div>
                  )}

                  <div className="set-pref">
                    <span className="k">{L2({ en: 'Plan', ru: 'Тариф' })}</span>
                    <div>
                      <span className="tag" data-billing-plan={subscription.plan_slug}>
                        {subscription.plan_slug}
                      </span>
                    </div>
                  </div>
                  <div className="set-pref">
                    <span className="k">{L2({ en: 'Status', ru: 'Статус' })}</span>
                    <div>
                      <span className="chip" data-billing-status={subscription.status}>
                        {subscription.status}
                      </span>
                    </div>
                  </div>
                  <div className="set-pref">
                    <span className="k">{L2({ en: 'Renews', ru: 'Продление' })}</span>
                    <div>
                      <span className="help" style={{ margin: 0 }}>
                        {new Date(subscription.current_period_end).toLocaleDateString()}
                        {subscription.cancel_at_period_end &&
                          ` · ${L2({ en: 'cancels at period end', ru: 'отменится в конце периода' })}`}
                      </span>
                    </div>
                  </div>

                  {portalErr && (
                    <div className="help" style={{ marginTop: 8, color: 'var(--danger, #c0392b)' }}>
                      {portalErr}
                    </div>
                  )}
                  <div className="set-foot">
                    <button
                      type="button"
                      className="btn"
                      disabled={portalBusy}
                      onClick={() => void managePortal()}
                    >
                      {portalBusy
                        ? L2({ en: 'Opening…', ru: 'Открываем…' })
                        : L2({ en: 'Manage subscription', ru: 'Управлять подпиской' })}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <p className="set-stat tnum" style={{ marginBottom: 8 }}>
                    <b>{L2({ en: 'Free plan', ru: 'Бесплатный тариф' })}</b>
                  </p>
                  <p className="help" style={{ margin: 0 }}>
                    {L2({
                      en: 'You’re on the free plan. Subscribe to let us handle the keys, routing, and infra.',
                      ru: 'Вы на бесплатном тарифе. Оформите подписку — и мы возьмём ключи, маршрутизацию и инфраструктуру на себя.',
                    })}
                  </p>
                  <div className="set-foot">
                    <Link className="btn btn-primary" href="/plans">
                      {L2({ en: 'View plans', ru: 'Смотреть тарифы' })}
                    </Link>
                  </div>
                </>
              )}
            </section>
          )}

          {/* 3 · Appearance */}
          {tab === 'appearance' && (
            <section className="card" aria-labelledby="appearance-h">
              <h3 className="card-title" id="appearance-h">
                {t('set.appearance')}
              </h3>
              <p className="card-desc">
                {L2({
                  en: 'Theme and interface language for this device.',
                  ru: 'Тема и язык интерфейса для этого устройства.',
                })}
              </p>

              <div className="set-pref">
                <span className="k">{t('set.theme')}</span>
                <div>
                  <div className="seg" style={{ margin: 0 }}>
                    <button
                      type="button"
                      className={theme === 'dark' ? 'on' : ''}
                      onClick={() => setTheme('dark')}
                    >
                      <span className="swatch swatch-dark" aria-hidden="true" />
                      {t('set.theme.dark')}
                    </button>
                    <button
                      type="button"
                      className={theme === 'light' ? 'on' : ''}
                      onClick={() => setTheme('light')}
                    >
                      <span className="swatch swatch-light" aria-hidden="true" />
                      {t('set.theme.light')}
                    </button>
                  </div>
                  <p className="why">
                    {L2({
                      en: 'Companions are used at night — dark is default.',
                      ru: 'Компаньона чаще используют вечером — тёмная тема по умолчанию.',
                    })}
                  </p>
                </div>
              </div>

              <div className="set-pref">
                <span className="k">{t('set.language')}</span>
                <div>
                  <div className="seg" style={{ margin: 0 }}>
                    <button
                      type="button"
                      className={lang === 'en' ? 'on' : ''}
                      onClick={() => setLang('en')}
                    >
                      English
                    </button>
                    <button
                      type="button"
                      className={lang === 'ru' ? 'on' : ''}
                      onClick={() => setLang('ru')}
                    >
                      Русский
                    </button>
                  </div>
                  <p className="why">
                    {L2({
                      en: 'Applies to the interface. Conversations keep their own language.',
                      ru: 'Относится к интерфейсу. Язык диалога не меняется.',
                    })}
                  </p>
                </div>
              </div>
            </section>
          )}

          {/* 4 · Key vault */}
          {tab === 'vault' && (
            <section className="card" aria-labelledby="vault-h">
              <h3 className="card-title" id="vault-h">
                {t('set.vault')}
              </h3>

              {!loaded ? (
                <div className="help" style={{ margin: 0 }}>
                  {t('set.vault.hint')}
                </div>
              ) : (
                <>
                  {providers.length === 0 ? (
                    <p className="card-desc" style={{ marginBottom: 8 }}>
                      {t('set.vault.status')}
                    </p>
                  ) : (
                    <p className="set-stat tnum">
                      <b>{providers.length}</b>{' '}
                      {L2({
                        en: `key${providers.length === 1 ? '' : 's'} connected`,
                        ru: 'ключей подключено',
                      })}
                    </p>
                  )}

                  <ProviderKeyList
                    providers={providers}
                    activeProviderId={activeProvider?.providerId}
                    onSetActive={setActive}
                    onRemove={(id) => setConfirmingRemove(id)}
                    busy={addBusy}
                  />

                  {/* Per-row remove confirm — re-mounted inline so the existing
                      "Remove this key?" copy and confirm flow keep their CSS
                      hooks. ProviderKeyList handles the Set as active /
                      Remove buttons; the confirm is keyed off the local
                      `confirmingRemove` state. */}
                  {confirmingRemove && (
                    <div className="set-confirm" style={{ marginTop: 8 }}>
                      <span className="help">
                        {L2({ en: 'Remove this key?', ru: 'Удалить этот ключ?' })}
                      </span>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger-ghost"
                        onClick={async () => {
                          const id = confirmingRemove;
                          setConfirmingRemove(null);
                          if (id) await remove(id);
                        }}
                      >
                        {L2({ en: 'Yes, remove', ru: 'Да, удалить' })}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        onClick={() => setConfirmingRemove(null)}
                      >
                        {L2({ en: 'Cancel', ru: 'Отмена' })}
                      </button>
                    </div>
                  )}

                  {/* Add a key / Add another key — same button, two contexts.
                      The empty state (no rows) shows the primary CTA; the
                      filled state shows it as a secondary action. No unlock
                      gate — keys live server-side now, so adding one is always
                      available. */}
                  <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                    <button
                      type="button"
                      className={`btn ${providers.length === 0 ? 'btn-primary' : 'btn-sm'}`}
                      onClick={() => {
                        setAddError(null);
                        setAddOpen(true);
                      }}
                    >
                      {providers.length === 0 ? t('set.vault.add_key') : t('set.vault.add_another')}
                    </button>
                  </div>

                  {addError && (
                    <div
                      className="alt-line"
                      style={{ marginTop: 8, color: 'var(--warn, #d4a23a)' }}
                    >
                      <span>{addError}</span>
                    </div>
                  )}

                  <p className="help" style={{ marginTop: 8 }}>
                    {t('set.vault.hint')}
                  </p>
                  {/* Docs pointer (not disclosure): the full key-storage detail
                      lives in SECURITY.md. Rendered as a labeled pointer rather
                      than a link because no docs URL is hosted yet — becomes a
                      real <Link> once SECURITY.md is served at a stable URL. */}
                  <span className="help" style={{ display: 'inline-block', marginTop: 4 }}>
                    {t('set.vault.docs')}
                  </span>
                </>
              )}

              <AddProviderModal
                open={addOpen}
                onClose={() => {
                  if (!addBusy) setAddOpen(false);
                }}
                onSubmit={addKey}
                title={providers.length === 0 ? t('set.vault.add_key') : t('set.vault.add_another')}
                submitLabel={t('set.vault.add_key')}
                busy={addBusy}
              />
            </section>
          )}

          {/* 4b · Integrations — external messengers (Telegram first). The bot
              token is server-side envelope-encrypted (honestly disclosed); BYOK
              keys bound at handshake are sealed from this browser. */}
          {tab === 'integrations' && <IntegrationsSection />}

          {/* 5 · Your data
              Export + Wipe are presented as honest, disabled "upcoming"
              affordances: no device-wipe or export endpoint exists in the API
              yet (per-persona wipe lives in Memory). The buttons are disabled
              and labelled so the UI never claims a capability it can't back. */}
          {tab === 'data' && (
            <section className="card" aria-labelledby="data-h">
              <h3 className="card-title" id="data-h">
                {t('set.data')}
              </h3>
              <p className="card-desc">{t('set.data.hint')}</p>

              <div className="grid grid-2">
                <div className="data-block">
                  <h4>{L2({ en: 'Export', ru: 'Экспорт' })}</h4>
                  <p>
                    {L2({
                      en: 'Download memory, conversations, and settings as a portable archive.',
                      ru: 'Скачать память, диалоги и настройки как переносимый архив.',
                    })}
                  </p>
                  <button type="button" className="btn" disabled>
                    {L2({ en: 'Export data', ru: 'Экспортировать данные' })}
                  </button>
                  <span className="data-soon">
                    {L2({ en: 'Upcoming — not available yet.', ru: 'Скоро — пока недоступно.' })}
                  </span>
                </div>

                <div className="data-block wipe-card">
                  <h4>{L2({ en: 'Wipe this device', ru: 'Очистить это устройство' })}</h4>
                  <p>
                    {L2({
                      en: 'Removes memory and conversations from this device. The server copy is not touched.',
                      ru: 'Удаляет память и диалоги с этого устройства. Серверная копия не затрагивается.',
                    })}
                  </p>
                  <button type="button" className="btn btn-danger-ghost" disabled>
                    {L2({ en: 'Wipe device data', ru: 'Очистить данные устройства' })}
                  </button>
                  <span className="data-soon">
                    {L2({ en: 'Upcoming — not available yet.', ru: 'Скоро — пока недоступно.' })}
                  </span>
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
