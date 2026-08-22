'use client';

import { type RoutingState, getRouting } from '@/lib/api-client';
import { useLang } from '@/lib/i18n';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

const KIND_LABEL: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  openrouter: 'OpenRouter',
  ollama: 'Ollama',
};

function chainNodeLabel(kind: string, t: (k: string) => string): string {
  return KIND_LABEL[kind] ?? kind;
}

const STATUS_KEY: Record<RoutingState['chain'][number]['status'], string> = {
  healthy: 'rt.s.healthy',
  standby: 'rt.s.standby',
  unavailable: 'rt.s.unavailable',
};

function money(n: number): string {
  return `$${n.toFixed(2)}`;
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function RoutingScreen() {
  const { t } = useLang();
  const router = useRouter();
  const [state, setState] = useState<RoutingState | null>(null);
  const [err, setErr] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setState(await getRouting());
      setErr(false);
    } catch {
      setErr(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const chain = state?.chain ?? [];
  const perProvider = state?.per_provider ?? [];
  const pct = Math.min(1, Math.max(0, state?.pct ?? 0));
  const firstOnIdx = chain.findIndex((n) => n.status === 'healthy');

  return (
    <main className="wrap rt-wrap">
      <div className="pagehead">
        <div className="pagehead__row">
          <div>
            <h1>{t('rt.title')}</h1>
            <p className="lede">{t('rt.lede')}</p>
          </div>
          <div className="pagehead__meta">
            <span className="chip chip--dim">{t('rt.configchip')}</span>
            {state?.langfuse_url ? (
              <a
                className="rt-langfuse mono"
                href={state.langfuse_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {t('rt.langfuse')}
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.6}
                  strokeLinecap="round"
                >
                  <path d="M7 17 17 7M9 7h8v8" />
                </svg>
              </a>
            ) : null}
          </div>
        </div>
      </div>

      <div className="rt-rows">
        {/* Row 1 — fallback chain */}
        <section className="panel panel--chain" aria-labelledby="rt-ch-h">
          <div className="panel__head">
            <span className="t" id="rt-ch-h">
              {t('rt.chain.title')}
            </span>
            <span className="sub">{t('rt.chain.sub')}</span>
          </div>
          <div className="panel__body">
            <div className="chain-dense">
              {chain.length === 0 ? (
                <span className="chain-note">{err ? t('rt.err') : t('rt.empty')}</span>
              ) : (
                chain.map((n, i) => (
                  <span key={`${n.kind}-${i}`} className="chain-dense__grp">
                    {i > 0 && <span className="conn" aria-hidden="true" />}
                    <span
                      className={`chip${i === firstOnIdx ? ' chip--on' : ''}${
                        n.status !== 'healthy' ? ' chip--dim' : ''
                      }`}
                    >
                      {chainNodeLabel(n.kind, t)}
                      <span className="tag">{n.model || t('rt.chain.perturn')}</span>
                    </span>
                  </span>
                ))
              )}
            </div>
            <p className="chain-note">{t('rt.chain.note.short')}</p>
            {/* The longer "how routing decides" detail — collapsed by default
                so the engineering depth is opt-in, not in a first-time user's
                face. BYOK-skip-self / 429-5xx-timeout / budget-first lives here. */}
            <details className="chain-details">
              <summary>{t('rt.chain.details')}</summary>
              <p className="chain-note">{t('rt.chain.note')}</p>
            </details>
          </div>
        </section>

        {/* Row 2 — budget */}
        <section className="panel panel--budget" aria-labelledby="rt-bg-h">
          <div className="panel__head">
            <span className="t" id="rt-bg-h">
              {t('rt.budget.title')}
            </span>
            <span className="sub">{t('rt.budget.sub')}</span>
          </div>
          <div className="panel__body">
            <div className="budget">
              <svg viewBox="0 0 120 120" aria-hidden="true" width={112} height={112}>
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
                  stroke={state?.hard_stop ? 'var(--danger)' : 'var(--purple)'}
                  strokeWidth="10"
                  strokeLinecap="round"
                  strokeDasharray={314.16}
                  strokeDashoffset={314.16 * (1 - pct)}
                  transform="rotate(-90 60 60)"
                />
                <text
                  x="60"
                  y="64"
                  textAnchor="middle"
                  fontFamily="var(--font)"
                  fontSize="22"
                  fill="var(--heading)"
                  fontWeight="300"
                >
                  {Math.round(pct * 100)}%
                </text>
              </svg>
              <div className="meta">
                <span className="pct tnum">{money(state?.spent_usd ?? 0)}</span>
                <span className="lbl tnum">
                  {t('rt.budget.oflimit', {
                    limit: money(state?.monthly_budget_usd ?? 0),
                    rem: money(state?.remaining_usd ?? 0),
                  })}
                </span>
                <div className="flags">
                  <span className="flag flag--warn">{t('rt.budget.flag.warn')}</span>
                  <span className="flag flag--stop">{t('rt.budget.flag.stop')}</span>
                </div>
              </div>
            </div>
            <p className="chain-note">{t('rt.budget.note')}</p>
            {state?.hard_stop ? (
              <span className="badge-stop rt-badge">{t('rt.budget.stop')}</span>
            ) : state?.warn ? (
              <span className="badge-warn rt-badge">{t('rt.budget.warn')}</span>
            ) : null}
          </div>
        </section>

        {/* Row 3 — providers table (full width) */}
        <section className="panel panel--full" aria-labelledby="rt-pv-h">
          <div className="panel__head">
            <span className="t" id="rt-pv-h">
              {t('rt.providers.title')}
            </span>
            <span className="sub">{t('rt.providers.sub')}</span>
          </div>
          <div className="panel__body rt-scroll">
            <table className="tbl rt">
              <thead>
                <tr>
                  <th>{t('rt.h.provider')}</th>
                  <th>{t('rt.h.model')}</th>
                  <th>{t('rt.h.status')}</th>
                  <th className="num">{t('rt.h.req')}</th>
                  <th className="num">{t('rt.tin')}</th>
                  <th className="num">{t('rt.tout')}</th>
                  <th className="num">{t('rt.h.cost')}</th>
                </tr>
              </thead>
              <tbody>
                {perProvider.map((r) => (
                  <tr
                    key={r.kind}
                    style={{ cursor: 'pointer' }}
                    title={t('rt.configure.hint')}
                    onClick={() => router.push('/onboarding')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        router.push('/onboarding');
                      }
                    }}
                  >
                    <td data-label={t('rt.h.provider')}>
                      <span className="prov">
                        <span
                          className={`dot${r.status === 'standby' ? ' standby' : ''}${
                            r.status === 'unavailable' ? ' off' : ''
                          }`}
                          title={t(STATUS_KEY[r.status])}
                        />
                        {KIND_LABEL[r.kind] ?? r.kind}
                        <span className={`hstat${r.status === 'unavailable' ? ' unavail' : ''}`}>
                          {t(STATUS_KEY[r.status])}
                        </span>
                      </span>
                    </td>
                    <td className="mono" data-label={t('rt.h.model')}>
                      {r.model || '—'}
                    </td>
                    <td data-label={t('rt.h.status')}>
                      <span className={`status${r.status === 'standby' ? ' warn' : ''}`}>
                        <span className="dot" />
                        {t(STATUS_KEY[r.status])}
                      </span>
                    </td>
                    <td className="num tnum" data-label={t('rt.h.req')}>
                      {r.requests}
                    </td>
                    <td className="num tnum" data-label={t('rt.tin')}>
                      {compact(r.tokens_in)}
                    </td>
                    <td className="num tnum" data-label={t('rt.tout')}>
                      {compact(r.tokens_out)}
                    </td>
                    <td className="num tnum" data-label={t('rt.h.cost')}>
                      {money(r.cost_usd)}
                    </td>
                  </tr>
                ))}
                {perProvider.length === 0 && (
                  <tr>
                    <td colSpan={7} className="help">
                      {err ? t('rt.err') : t('rt.empty')}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Row 4 — last fallback (full width, process-local) */}
        <section className="panel panel--full rt-lastfb" aria-labelledby="rt-lf-h">
          <div className="panel__head">
            <span className="t" id="rt-lf-h">
              {t('rt.lastfb.title')}
            </span>
            <span className="sub">{t('rt.lastfb.sub')}</span>
          </div>
          <div className="panel__body">
            <code className="mono">{state?.fallback_last_turn ?? t('rt.fb.none')}</code>
            <span className="chain-note">{t('rt.lastfb.lost')}</span>
          </div>
        </section>
      </div>

      <p className="rt-footnote">{t('rt.footnote')}</p>
    </main>
  );
}
