'use client';

import { useAuthCtx } from '@/lib/auth';
import { useLang } from '@/lib/i18n';
import Link from 'next/link';

// The marketing landing — a port of the Open Design `stillside-app-130d`
// index.html (8 sections + footer). Bilingual copy lives in `landing.*` i18n
// keys; unilingual literals (flowline chips, BYOK ключ/env/ollama/mock, mono
// labels, prices, 64%, $0.41, Free/Plus/Pro, the MIT tagline) are inlined
// here. The auth-aware TopBar is rendered by the page/layout, not here.
//
// CTA routing:
//  - hero "Start — free" + closing "Start — free" → /chat on hosted (lazy onboarding:
//    chat first, keys later from Settings), /onboarding on self-hosted (BYOK-first).
//  - hero "See plans" → /plans when billing is on, else the in-page #pricing anchor
//  - pricing Free → /onboarding; Plus/Pro → /plans (hidden on billing-off / self-hosted,
//    which surfaces landing.pricing.note instead)
//  - section 5 "Under the hood" (tech depth) is self-hosted only — hosted hides it so a
//    first-time visitor isn't met with the routing/BYOK engineering surface.

const FLOWLINE = [
  'landing.flowline.persona',
  'landing.flowline.chains',
  'landing.flowline.window',
  'landing.flowline.msg',
];
const CHAIN_CHIPS = [
  { labelKey: 'landing.chainchip.byok', on: true },
  { labelKey: 'landing.chainchip.env', on: false },
  { labelKey: 'landing.chainchip.ollama', on: false },
  { labelKey: 'landing.chainchip.fallback', on: false, dim: true },
];
const PROVIDERS = [
  { name: 'OpenAI', model: 'gpt-5-mini', reqs: '12', tokens: '18,402', cost: '$0.041', dot: 'on' },
  {
    name: 'Anthropic',
    model: 'claude-haiku-4.5',
    reqs: '6',
    tokens: '9,210',
    cost: '$0.027',
    dot: 'on',
  },
  {
    name: 'OpenRouter',
    model: 'llama-3.3-70b',
    reqs: '3',
    tokens: '5,108',
    cost: '$0.004',
    dot: 'on',
  },
  {
    name: 'Ollama · local',
    model: 'llama3.2:3b',
    reqs: '24',
    tokens: '31,022',
    cost: '$0.000',
    dot: 'standby',
  },
  { name: 'Local fallback', model: '—', reqs: '1', tokens: '0', cost: '$0.000', dot: 'off' },
] as const;

const STEPS = [
  { n: '01', h: 'landing.how.s1.h4', p: 'landing.how.s1.p' },
  { n: '02', h: 'landing.how.s2.h4', p: 'landing.how.s2.p' },
  { n: '03', h: 'landing.how.s3.h4', p: 'landing.how.s3.p' },
  { n: '04', h: 'landing.how.s4.h4', p: 'landing.how.s4.p' },
] as const;

const LIMITS = [
  { lead: 'landing.limits.1.lead', body: 'landing.limits.1.body' },
  { lead: 'landing.limits.2.lead', body: 'landing.limits.2.body' },
  { lead: 'landing.limits.3.lead', body: 'landing.limits.3.body' },
  { lead: 'landing.limits.4.lead', body: 'landing.limits.4.body' },
] as const;

export function HomeScreen() {
  const { t } = useLang();
  // Billing is hosted-only; on self-hosted instances the Plus/Pro checkout
  // can't be served, so those CTAs hide and an honest note shows instead.
  const billing = !!useAuthCtx().config?.features.billing;

  const plansCta = billing ? (
    <Link className="btn btn--ghost" href="/plans">
      {t('landing.hero.cta.plans')}
    </Link>
  ) : (
    <a className="btn btn--ghost" href="#pricing">
      {t('landing.hero.cta.plans')}
    </a>
  );

  return (
    <div className="landing">
      {/* ============ HERO ============ */}
      <section className="hero-stage wrap">
        <div className="hero-1">
          <div className="hero-copy copy">
            <span className="badge">
              <span className="dot" />
              {t('landing.hero.badge')}
            </span>
            <h1>{t('landing.hero.h1')}</h1>
            <p className="sub">{t('landing.hero.sub')}</p>
            <div className="hero-cta">
              <Link className="btn btn--primary" href={billing ? '/chat' : '/onboarding'}>
                {t('landing.hero.cta.start')}
              </Link>
              {plansCta}
            </div>
          </div>
          <div className="orb-stage" aria-hidden="true">
            <div className="orb">
              <span className="spark" style={{ top: '18%', left: '62%' }} />
              <span
                className="spark"
                style={{ top: '70%', left: '24%', width: '4px', height: '4px' }}
              />
              <span
                className="spark"
                style={{ top: '40%', left: '88%', width: '4px', height: '4px' }}
              />
            </div>
            <div className="bubble-ghost">
              <div className="who">{t('landing.hero.bubble.who')}</div>
              {t('landing.hero.bubble.msg')}
            </div>
          </div>
        </div>
      </section>

      {/* ============ 2. DIFFERENCE ============ */}
      <section className="section diff">
        <div className="wrap">
          <p className="eyebrow">{t('landing.diff.eyebrow')}</p>
          <h2>{t('landing.diff.h2')}</h2>
          <div className="diff__grid">
            <div className="diffcard diffcard--a">
              <h3>{t('landing.diff.a.h3')}</h3>
              <p>{t('landing.diff.a.p')}</p>
            </div>
            <div className="diffcard diffcard--b">
              <h3>Retellis</h3>
              <p>{t('landing.diff.b.p')}</p>
            </div>
          </div>
          <div className="flowline">
            {FLOWLINE.map((s, i) => (
              <span key={s} className="flowline__grp">
                <span className="step">{t(s)}</span>
                {i < FLOWLINE.length - 1 && <span className="arr">→</span>}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ============ 3. WHY RETELLIS ============ */}
      <section className="section">
        <div className="wrap">
          <p className="eyebrow">{t('landing.why.eyebrow')}</p>
          <h2>{t('landing.why.h2')}</h2>

          <div className="tier-a">
            <article className="acard">
              <div className="acard__viz">
                <div className="viz-chain">
                  <svg viewBox="0 0 320 96" aria-hidden="true">
                    <line
                      x1="14"
                      y1="58"
                      x2="306"
                      y2="58"
                      stroke="var(--border-strong)"
                      strokeWidth="1"
                    />
                    <g fill="var(--surface)" stroke="var(--border-strong)" strokeWidth="1">
                      <circle cx="28" cy="58" r="6" />
                      <circle cx="96" cy="58" r="6" />
                      <circle cx="164" cy="58" r="6" />
                      <circle cx="232" cy="58" r="6" />
                      <circle cx="292" cy="58" r="6" />
                    </g>
                    <g fontFamily="var(--mono)" fontSize="9" fill="var(--label)">
                      <text x="28" y="84" textAnchor="middle">
                        e1
                      </text>
                      <text x="96" y="84" textAnchor="middle">
                        e2
                      </text>
                      <text x="164" y="84" textAnchor="middle">
                        e3
                      </text>
                      <text x="232" y="84" textAnchor="middle">
                        e4
                      </text>
                      <text x="292" y="84" textAnchor="middle">
                        e5
                      </text>
                    </g>
                    <g fontFamily="var(--mono)" fontSize="8" fill="var(--purple)">
                      <rect
                        x="50"
                        y="14"
                        width="44"
                        height="14"
                        rx="4"
                        fill="var(--purple-soft)"
                        stroke="rgba(139,120,255,.3)"
                      />
                      <text x="72" y="24" textAnchor="middle">
                        salience
                      </text>
                      <rect
                        x="182"
                        y="14"
                        width="44"
                        height="14"
                        rx="4"
                        fill="var(--purple-soft)"
                        stroke="rgba(139,120,255,.3)"
                      />
                      <text x="204" y="24" textAnchor="middle">
                        salience
                      </text>
                    </g>
                  </svg>
                </div>
              </div>
              <h3>{t('landing.why.a1.h3')}</h3>
              <p>{t('landing.why.a1.p')}</p>
            </article>

            <article className="acard">
              <div className="acard__viz">
                <div className="viz-keys">
                  <span className="lock">
                    <span className="m">sk-••••••••</span>
                    <span className="t">3a2f</span>
                  </span>
                </div>
              </div>
              <h3>{t('landing.why.a2.h3')}</h3>
              <p>{t('landing.why.a2.p')}</p>
            </article>

            <article className="acard">
              <div className="acard__viz">
                <div className="viz-fam" aria-hidden="true">
                  <span className="c c1" />
                  <span className="c c2" />
                </div>
              </div>
              <h3>{t('landing.why.a3.h3')}</h3>
              <p>{t('landing.why.a3.p')}</p>
            </article>

            <article className="acard">
              <div className="acard__viz">
                <div className="viz-avatars" aria-hidden="true">
                  <span className="av av1" />
                  <span className="av av2" />
                  <span className="av av3" />
                  <span className="av av4" />
                  <span className="av av5" />
                </div>
              </div>
              <h3>{t('landing.why.a4.h3')}</h3>
              <p>{t('landing.why.a4.p')}</p>
            </article>
          </div>

          <div className="tier-b">
            <div className="bcard">
              <span className="ic">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M5 4h14v16l-7-3-7 3z" />
                </svg>
              </span>
              <div>
                <h4>{t('landing.why.b1.h4')}</h4>
                <p>{t('landing.why.b1.p')}</p>
              </div>
            </div>
            <div className="bcard">
              <span className="ic">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
                </svg>
              </span>
              <div>
                <h4>{t('landing.why.b2.h4')}</h4>
                <p>{t('landing.why.b2.p')}</p>
              </div>
            </div>
            <div className="bcard">
              <span className="ic">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M4 12c4-6 12-6 16 0M4 12c4 6 12 6 16 0" />
                </svg>
              </span>
              <div>
                <h4>{t('landing.why.b3.h4')}</h4>
                <p>{t('landing.why.b3.p')}</p>
              </div>
            </div>
            <div className="bcard">
              <span className="ic">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <rect x="5" y="10" width="14" height="10" rx="2" />
                  <path d="M8 10V7a4 4 0 0 1 8 0v3" />
                </svg>
              </span>
              <div>
                <h4>{t('landing.why.b4.h4')}</h4>
                <p>{t('landing.why.b4.p')}</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============ 4. HOW IT WORKS ============ */}
      <section className="section section--tinted">
        <div className="wrap">
          <p className="eyebrow">{t('landing.how.eyebrow')}</p>
          <h2>{t('landing.how.h2')}</h2>
          <div className="steps">
            {STEPS.map((s) => (
              <div className="step" key={s.n}>
                <div className="n">{s.n}</div>
                <h4>{t(s.h)}</h4>
                <p>{t(s.p)}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============ 5. TECH DEPTH (self-hosted only — hosted hides this section) ============ */}
      {!billing && (
        <section className="section tech">
          <div className="wrap">
            <p className="eyebrow">{t('landing.tech.eyebrow')}</p>
            <h2>{t('landing.tech.h2')}</h2>

            <div className="tech__grid">
              <div className="panel">
                <div className="panel__head">
                  <span className="t">{t('landing.tech.chain.title')}</span>
                  <span className="sub">ordered · local fallback last</span>
                </div>
                <div className="panel__body">
                  <div className="chain-dense">
                    {CHAIN_CHIPS.map((c, i) => (
                      <span key={c.labelKey} className="flowline__grp">
                        <span
                          className={`chip${c.on ? ' chip--on' : ''}${c.dim ? ' chip--dim' : ''}`}
                        >
                          {t(c.labelKey)}
                        </span>
                        {i < CHAIN_CHIPS.length - 1 && <span className="conn" />}
                      </span>
                    ))}
                  </div>
                  <p className="chain-note">{t('landing.tech.chain.note')}</p>

                  <table className="providers">
                    <thead>
                      <tr>
                        <th>{t('landing.tech.table.provider')}</th>
                        <th>{t('landing.tech.table.model')}</th>
                        <th className="num">{t('landing.tech.table.reqs')}</th>
                        <th className="num">{t('landing.tech.table.tokens')}</th>
                        <th className="num">{t('landing.tech.table.cost')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {PROVIDERS.map((p) => (
                        <tr key={p.name}>
                          <td>
                            <span className="prov">
                              <span className={`dot${p.dot === 'on' ? '' : ` ${p.dot}`}`} />
                              {p.name}
                            </span>
                          </td>
                          <td className="mono">{p.model}</td>
                          <td className="num">{p.reqs}</td>
                          <td className="num">{p.tokens}</td>
                          <td className="num">{p.cost}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="panel">
                <div className="panel__head">
                  <span className="t">{t('landing.tech.budget.title')}</span>
                  <span className="sub">soft 80% · hard 100%</span>
                </div>
                <div className="panel__body">
                  <div className="budget">
                    <svg viewBox="0 0 120 120" aria-hidden="true">
                      <circle
                        cx="60"
                        cy="60"
                        r="50"
                        fill="none"
                        stroke="var(--border)"
                        strokeWidth="10"
                      />
                      <circle
                        cx="60"
                        cy="60"
                        r="50"
                        fill="none"
                        stroke="var(--purple)"
                        strokeWidth="10"
                        strokeLinecap="round"
                        strokeDasharray="201 314"
                        transform="rotate(-90 60 60)"
                      />
                      <text
                        x="60"
                        y="64"
                        textAnchor="middle"
                        fontFamily="var(--sans)"
                        fontSize="22"
                        fill="var(--heading)"
                        fontWeight="300"
                      >
                        64%
                      </text>
                    </svg>
                    <div className="meta">
                      <span className="pct tnum">$0.41</span>
                      <span className="lbl">{t('landing.tech.budget.lbl')}</span>
                      <div className="flags">
                        <span className="flag">soft 80%</span>
                        <span className="flag flag--warn">warn</span>
                        <span className="flag flag--stop">hard-stop</span>
                      </div>
                    </div>
                  </div>
                  <p className="chain-note">{t('landing.tech.budget.note')}</p>
                </div>
              </div>
            </div>

            <div className="tech__foot">
              <code>{t('landing.tech.foot3')}</code>
            </div>
          </div>
        </section>
      )}

      {/* ============ 6. PRICING ============ */}
      <section className="section" id="pricing">
        <div className="wrap">
          <p className="eyebrow">{t('landing.pricing.eyebrow')}</p>
          <h2>{t('landing.pricing.h2')}</h2>
          <div className="pricing">
            <div className="plan">
              <span className="nm">Free</span>
              <span className="price">
                $0<small> {t('landing.pricing.free.unit')}</small>
              </span>
              <ul>
                <li>{t('landing.pricing.free.f1')}</li>
                <li>{t('landing.pricing.free.f2')}</li>
                <li>{t('landing.pricing.free.f3')}</li>
              </ul>
              <Link className="btn btn--ghost" href="/onboarding">
                {t('landing.pricing.free.cta')}
              </Link>
            </div>
            <div className="plan plan--feat">
              <span className="nm">Plus</span>
              <span className="price">
                $12<small> {t('landing.pricing.plus.unit')}</small>
              </span>
              <ul>
                <li>{t('landing.pricing.plus.f1')}</li>
                <li>{t('landing.pricing.plus.f2')}</li>
                <li>{t('landing.pricing.plus.f3')}</li>
              </ul>
              {billing ? (
                <Link className="btn btn--accent" href="/plans">
                  {t('landing.pricing.plus.cta')}
                </Link>
              ) : (
                <span className="btn btn--accent btn--disabled" aria-disabled="true">
                  {t('landing.pricing.plus.cta')}
                </span>
              )}
            </div>
            <div className="plan">
              <span className="nm">Pro</span>
              <span className="price">
                $24<small> {t('landing.pricing.pro.unit')}</small>
              </span>
              <ul>
                <li>{t('landing.pricing.pro.f1')}</li>
                <li>{t('landing.pricing.pro.f2')}</li>
                <li>{t('landing.pricing.pro.f3')}</li>
              </ul>
              {billing ? (
                <Link className="btn btn--ghost" href="/plans">
                  {t('landing.pricing.pro.cta')}
                </Link>
              ) : (
                <span className="btn btn--ghost btn--disabled" aria-disabled="true">
                  {t('landing.pricing.pro.cta')}
                </span>
              )}
            </div>
          </div>
          {!billing && <p className="chain-note pricing__note">{t('landing.pricing.note')}</p>}
        </div>
      </section>

      {/* ============ 7. HONEST LIMITS ============ */}
      <section className="section section--dense">
        <div className="wrap">
          <div className="limits">
            <p className="eyebrow" style={{ marginBottom: 0 }}>
              {t('landing.limits.eyebrow')}
            </p>
            <h2 className="limits__h">{t('landing.limits.h2')}</h2>
            <ul>
              {LIMITS.map((l, i) => (
                <li key={i}>
                  <strong>{t(l.lead)}</strong>
                  {t(l.body)}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* ============ 8. CLOSING ============ */}
      <section className="closing wrap">
        <h2>{t('landing.closing.h2')}</h2>
        <Link className="btn btn--primary" href={billing ? '/chat' : '/onboarding'}>
          {t('landing.hero.cta.start')}
        </Link>
      </section>

      <footer>
        <div className="wrap foot__row">
          <span className="logo">
            <span className="logo__mark" />
            Retellis
          </span>
          <nav className="foot__links">
            <a href="#">{t('landing.foot.source')}</a>
            <a href="#">{t('landing.foot.docs')}</a>
            <a href="#">{t('landing.foot.security')}</a>
            <a href="#pricing">{t('landing.foot.pricing')}</a>
            <a href="#">{t('landing.foot.contact')}</a>
          </nav>
          <span className="tnum foot__tag">Retellis · open-source · MIT</span>
        </div>
      </footer>
    </div>
  );
}
