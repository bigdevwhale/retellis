'use client';

import { createCheckout, listPlans } from '@/lib/api-client';
import { useAuthCtx } from '@/lib/auth';
import { PLANS } from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';
import type { Plan as ServerPlan } from '@ai-companion/contracts';
import Link from 'next/link';
import { useEffect, useState } from 'react';

// The fixture plan id (``plus`` / ``pro``) maps to a server plan slug by geo:
// WW → Paddle (USD), RU → ЮKassa (RUB, 54-ФЗ). Provider routing is by
// billing_country (manual here), NOT by IP — IP is unreliable behind a VPN.
type Geo = 'WW' | 'RU';

const slugFor = (id: string, geo: Geo): string | null => {
  if (id === 'free') return null;
  return `${id}_${geo.toLowerCase()}`;
};

// Format a server plan's price (minor units) + currency for display. RUB →
// ``690₽``; anything else → ``$12``. Falls back to the fixture string when we
// don't have a server price (e.g. the fetch failed — non-billing or offline).
function serverPrice(plan: ServerPlan | undefined, fallback: string): string {
  if (!plan) return fallback;
  const major = plan.price_cents / 100;
  if (plan.currency === 'RUB') return `${major % 1 === 0 ? major.toFixed(0) : major.toFixed(2)}₽`;
  return `$${major % 1 === 0 ? major.toFixed(0) : major.toFixed(2)}`;
}

export function PlansScreen() {
  const { t, L2 } = useLang();
  // Billing is a hosted-only capability (gated `and is_hosted` server-side in
  // auth/bootstrap.py, so features.billing is False on every self-hosted
  // instance regardless of env). Rendering paid plans + a checkout CTA here
  // would assert a capability the deployment can't serve — "disclose, don't
  // perform". So when billing is off we show an honest "not available on this
  // instance" panel instead of the grid. `loading` is checked first so a
  // hosted deployment (billing on) doesn't flash the not-available panel
  // during boot before /v1/config resolves, and a self-hosted one doesn't
  // flash the plans grid before the flag resolves either.
  const { config, loading } = useAuthCtx();
  const billing = !!config?.features.billing;

  const [bill, setBill] = useState<'monthly' | 'yearly'>('monthly');
  const mul = bill === 'yearly' ? 0.8 : 1;
  const perSuf =
    bill === 'yearly'
      ? L2({ en: '/mo billed yearly', ru: '/мес, раз в год' })
      : L2({ en: '/month', ru: '/мес' });

  const [geo, setGeo] = useState<Geo>('WW');
  // Server-side plan catalogue for the selected geo — the authoritative
  // prices + currency (RUB for RU). Best-effort: if the fetch fails we fall
  // back to the fixture USD prices, so the grid still renders.
  const [serverPlans, setServerPlans] = useState<Record<string, ServerPlan>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!billing) return;
    let cancelled = false;
    listPlans(geo)
      .then((plans) => {
        if (cancelled) return;
        const byBase: Record<string, ServerPlan> = {};
        for (const p of plans) {
          // slug shape: ``plus_ww`` / ``pro_ru`` → base id is the part before ``_``.
          const base = p.slug.split('_')[0];
          if (base) byBase[base] = p;
        }
        setServerPlans(byBase);
      })
      .catch(() => {
        // Non-fatal — the fixture prices stand in.
      });
    return () => {
      cancelled = true;
    };
  }, [billing, geo]);

  const subscribe = async (id: string) => {
    if (id === 'free') return;
    const slug = slugFor(id, geo);
    if (!slug) return;
    setErr(null);
    setBusy(id);
    try {
      const session = await createCheckout({ plan_slug: slug, billing_country: geo });
      // Redirect to the provider's hosted checkout — no card data is collected
      // on our side (PCI-scope SAQ-A). The checkout callback redirect does NOT
      // mutate state; webhooks are the single source of truth, so on return
      // the app re-reads the subscription + Principal.
      window.location.href = session.redirect_url;
    } catch {
      setErr(
        L2({
          en: 'Checkout is unavailable right now. Try again later.',
          ru: 'Оплата сейчас недоступна. Попробуйте позже.',
        }),
      );
      setBusy(null);
    }
  };

  // Config not resolved yet — render nothing rather than flash either state.
  if (loading) return null;

  if (!billing) {
    return (
      <div className="wrap">
        <div className="hero" style={{ marginBottom: 8 }}>
          <h1>{t('pl.unavail.h1')}</h1>
          <p>{t('pl.unavail.p')}</p>
          <Link className="btn btn-primary" href="/">
            {t('pl.unavail.cta')}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="topbar">
        <h2>{t('pl.title')}</h2>
        <span className="sub">{t('pl.sub')}</span>
      </div>
      <div className="wrap">
        <div className="hero" style={{ marginBottom: 8 }}>
          <h1>{t('pl.h1')}</h1>
          <p>{t('pl.p')}</p>
        </div>
        <div className="plan-toggle">
          <button
            type="button"
            className={bill === 'monthly' ? 'on' : ''}
            onClick={() => setBill('monthly')}
          >
            {t('pl.monthly')}
          </button>
          <button
            type="button"
            className={bill === 'yearly' ? 'on' : ''}
            onClick={() => setBill('yearly')}
          >
            {t('pl.yearly')}
          </button>
        </div>
        {/* Geo / payment-region selector. RU → ЮKassa (RUB, 54-ФЗ receipt);
            anything else → Paddle (USD). Manual — NOT IP-derived. */}
        <div className="plan-toggle" style={{ marginTop: 12 }}>
          <button type="button" className={geo === 'WW' ? 'on' : ''} onClick={() => setGeo('WW')}>
            {L2({ en: 'International (card)', ru: 'Зарубеж (карта)' })}
          </button>
          <button type="button" className={geo === 'RU' ? 'on' : ''} onClick={() => setGeo('RU')}>
            {L2({ en: 'Russia (МИР / СБП)', ru: 'Россия (МИР / СБП)' })}
          </button>
        </div>
        {geo === 'RU' && (
          <p className="sub" style={{ marginTop: 8 }}>
            {L2({
              en: 'Paid in RUB via ЮKassa. A 54-ФЗ online receipt is issued automatically.',
              ru: 'Оплата в рублях через ЮKassa. Онлайн-чек 54-ФЗ формируется автоматически.',
            })}
          </p>
        )}
        {err && (
          <p className="sub" style={{ marginTop: 8, color: 'var(--danger, #c0392b)' }}>
            {err}
          </p>
        )}
        <div className="grid grid-3 stagger">
          {PLANS.map((p, i) => {
            const sp = serverPlans[p.id];
            const fallback =
              p.id === 'free' ? p.price : `$${Math.round(Number.parseInt(p.price.slice(1)) * mul)}`;
            const price = p.id === 'free' ? p.price : serverPrice(sp, fallback);
            const featured = p.cls === 'featured';
            return (
              <div
                key={p.id}
                className={`plan-card${p.cls ? ` ${p.cls}` : ''}`}
                style={{ animationDelay: `${i * 80}ms` }}
              >
                {featured && <div className="featured-badge">{t('pl.badge')}</div>}
                <div className="pname">{p.name}</div>
                <div className="price">
                  {price}
                  <small>
                    {' '}
                    {L2(p.per)}
                    {p.id === 'free' ? '' : ` ${perSuf}`}
                  </small>
                </div>
                <div className="pdesc">{L2(p.desc)}</div>
                <ul>
                  {p.features.map((f, j) => (
                    <li key={j}>
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={2.4}
                      >
                        <path d="M20 6L9 17l-5-5" />
                      </svg>
                      {L2(f)}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className={`btn${featured ? ' btn-primary' : ''}`}
                  disabled={p.disabled || busy !== null}
                  onClick={() => subscribe(p.id)}
                >
                  {busy === p.id ? L2({ en: 'Redirecting…', ru: 'Перенаправляем…' }) : t(p.ctaKey)}
                </button>
              </div>
            );
          })}
        </div>
        <div className="plan-note">
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.7}
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16h.01" />
          </svg>
          <div>
            <b style={{ color: 'var(--heading)' }}>{t('pl.note.b')}</b>{' '}
            <span>{t('pl.note.s')}</span>
          </div>
        </div>
      </div>
    </>
  );
}
