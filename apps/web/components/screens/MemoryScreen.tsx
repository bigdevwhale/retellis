'use client';

import {
  type EventChainRecord,
  type MemoryRecord,
  type MemoryShareRecord,
  addMemoryShare,
  listMemories,
  listMemoryShares,
  recallMemory,
  removeMemoryShare,
  wipePersonaMemory,
} from '@/lib/api-client';
import { personaById } from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';
import { useStore } from '@/lib/store';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

// How many corpus-derived theme chips to surface. "All" is always first; the
// rest are the most frequent tags across this persona's active memories, so the
// chips reflect what the user has actually been talking about — not a hardcoded
// taxonomy. Clicking a chip filters the list to memories carrying that tag.
const MAX_THEME_CHIPS = 8;

function countTags(memories: MemoryRecord[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const m of memories) {
    for (const tag of m.tags) {
      const k = tag.toLowerCase().trim();
      if (!k) continue;
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
  }
  return counts;
}

function topThemes(memories: MemoryRecord[]): string[] {
  const counts = countTags(memories);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, MAX_THEME_CHIPS)
    .map(([tag]) => tag);
}

// Relative "updated N days ago" string for the per-memory timestamp. The
// memories are server-side synthesis, so recency signals which facts are still
// current vs. something captured once weeks ago.
function relativeUpdated(
  updatedAt: string,
  t: (k: string, vars?: Record<string, string | number>) => string,
): string {
  const then = Date.parse(updatedAt);
  if (Number.isNaN(then)) return '';
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return t('mem.rel.today');
  if (days === 1) return t('mem.rel.yesterday');
  if (days < 7) return t('mem.rel.daysago', { n: days });
  if (days < 30) return t('mem.rel.weeksago', { n: Math.floor(days / 7) });
  return t('mem.rel.monthsago', { n: Math.floor(days / 30) });
}

// Salience as filled stars (0..5) + the numeric bar — two redundant cues so
// high-salience memories stand out at a glance even before reading the content.
function renderStars(salience: number) {
  const filled = Math.round(salience * 5);
  return (
    <span className="stars tnum" aria-hidden>
      {[0, 1, 2, 3, 4].map((k) => (
        <span key={k} className={k < filled ? 'on' : 'off'}>
          ★
        </span>
      ))}
    </span>
  );
}

export function MemoryScreen() {
  const { t, L2 } = useLang();
  const activePersonaId = useStore((s) => s.activePersonaId);
  // Select the stable *function* (s.personas), not its result. `personas()`
  // returns a fresh array each call ([...PERSONAS, ...customPersonas]); selecting
  // that array directly makes zustand's useSyncExternalStore see a new snapshot
  // every render → infinite re-render loop (React error #185). Call it in the
  // render body instead, like PersonaScreen does.
  const personas = useStore((s) => s.personas);
  const personaList = personas();
  const setActivePersona = useStore((s) => s.setActivePersona);
  // Family slice — used when persona is `fam`. The filter mirrors the server
  // predicate the family therapist uses in the stream (solo reads shared +
  // own private; joint reads shared only).
  const family = useStore((s) => s.family);
  const familyMembers = useStore((s) => s.familyMembers);
  const activeFamilyMemberId = useStore((s) => s.activeFamilyMemberId);
  const familySessionMode = useStore((s) => s.familySessionMode);
  const setActiveFamilyMemberId = useStore((s) => s.setActiveFamilyMemberId);
  const setFamilySessionMode = useStore((s) => s.setFamilySessionMode);

  // Local override so the user can browse any persona's memory here without
  // first opening a chat with that persona. Defaults to the active persona.
  const [personaId, setPersonaId] = useState<string>(activePersonaId);

  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [theme, setTheme] = useState<string | null>(null); // null = "All"
  // OD memory.html segmented view toggle. Default is "memories" (always
  // available); "chains" needs a recall probe (the server exposes no "list all
  // chains" endpoint, so we don't fabricate a default chain list — honest).
  const [view, setView] = useState<'chains' | 'memories' | 'shares'>('memories');

  const [probe, setProbe] = useState('');
  const [chains, setChains] = useState<EventChainRecord[] | null>(null);
  const [busy, setBusy] = useState(false);

  // Cross-persona live memory shares where this persona is the DONOR. The
  // receiver's read paths union the donor's memories while the link exists.
  const [shares, setShares] = useState<MemoryShareRecord[]>([]);
  const [sharePick, setSharePick] = useState('');
  const [shareErr, setShareErr] = useState(false);

  // Two-step confirm for the persona memory wipe (mirrors OnboardingScreen's
  // confirmingReset). Separate `wipeBusy` so it never blocks the recall probe.
  const [confirmingWipe, setConfirmingWipe] = useState(false);
  const [wipeBusy, setWipeBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const ff =
        personaId === 'fam' && family && activeFamilyMemberId
          ? {
              familyId: family.id,
              visibility: familySessionMode,
              participantUserId: activeFamilyMemberId,
            }
          : undefined;
      const [mems, shrs] = await Promise.all([
        listMemories(personaId, ff),
        listMemoryShares(personaId),
      ]);
      setMemories(mems);
      setShares(shrs);
    } catch {
      setMemories([]);
      setShares([]);
    } finally {
      setLoaded(true);
    }
  }, [personaId, family, familySessionMode, activeFamilyMemberId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Reset the recall probe + theme filter when switching persona — a stale
  // view for another persona is misleading. personaId is intentionally a
  // dependency: the effect runs *because* it changed (the reset-on-change
  // idiom), not because the body reads it, so the exhaustive-deps rule is a
  // false positive here.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset-on-change idiom
  useEffect(() => {
    setChains(null);
    setProbe('');
    setTheme(null);
  }, [personaId]);

  const doRecall = async () => {
    if (!probe.trim()) return;
    setBusy(true);
    try {
      setChains(await recallMemory(personaId, probe.trim()));
    } catch {
      setChains([]);
    } finally {
      setBusy(false);
    }
  };

  // Themes come from the loaded memories themselves, so they shift with what
  // the user has been discussing. Re-derived on every load; "All" is always
  // first and clearing the filter returns to it.
  const themes = useMemo(() => topThemes(memories), [memories]);

  const visible = useMemo(
    () => (theme === null ? memories : memories.filter((m) => m.tags.includes(theme))),
    [memories, theme],
  );

  const onPick = (id: string) => {
    setPersonaId(id);
    // Keep the store in sync so opening chat later continues from the same
    // persona the user is currently inspecting.
    setActivePersona(id);
  };

  // Receivers this persona is already sharing with — excludes them from the
  // picker so the list shows only *new* targets.
  const sharedReceiverIds = useMemo(
    () => new Set(shares.map((s) => s.receiver_persona_id)),
    [shares],
  );
  const shareCandidates = personaList.filter(
    (p) => p.id !== personaId && !sharedReceiverIds.has(p.id),
  );

  const addShare = async () => {
    if (!sharePick) return;
    if (sharePick === personaId) {
      setShareErr(true);
      return;
    }
    setShareErr(false);
    try {
      await addMemoryShare(personaId, sharePick);
      setSharePick('');
      await refresh();
    } catch {
      setShareErr(true);
    }
  };

  const removeShare = async (receiverId: string) => {
    try {
      await removeMemoryShare(personaId, receiverId);
      await refresh();
    } catch {
      // best-effort — a stale chip stays; the next persona switch refreshes.
    }
  };

  const doWipe = async () => {
    setWipeBusy(true);
    try {
      await wipePersonaMemory(personaId);
      await refresh();
    } catch {
      // best-effort — the list refreshes on next persona switch; surface nothing
      // scary in-product (the server op is idempotent and re-triable).
    } finally {
      setWipeBusy(false);
      setConfirmingWipe(false);
    }
  };

  return (
    <div className="page mem-page">
      <div className="col">
        {/* PAGEHEAD — OD memory.html. Statrow counts are honest: only memories
            + outgoing shares are known client-side (no "all chains/events"
            endpoint), so we don't fabricate event/chain counts. */}
        <div className="pagehead">
          <div className="eyebrow">{t('mem.eyebrow')}</div>
          <h1>{t('mem.title')}</h1>
          <p className="lede">{t('mem.lede')}</p>
          <div className="statrow tnum">
            <span className="n">{memories.length}</span>&nbsp;{t('mem.stat.memories')}
            <span className="sep">·</span>
            <span className="n">{shares.length}</span>&nbsp;{t('mem.stat.shares')}
          </div>
          <div className="mem-controls">
            <label htmlFor="mem-persona" className="sub">
              {t('mem.persona')}
            </label>
            <select
              id="mem-persona"
              className="input"
              value={personaId}
              onChange={(e) => onPick(e.target.value)}
            >
              {personaList.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {L2({ en: p.role.en, ru: p.role.ru })}
                </option>
              ))}
            </select>
            {personaId === 'fam' && family && (
              // Family-scope filter: mirrors the solo/joint predicate the
              // family therapist uses in the stream. Solo reads shared + the
              // selected member's own private; joint reads shared only.
              <>
                <label className="sub" htmlFor="mem-fam-member">
                  {L2({ en: 'Member', ru: 'Член семьи' })}
                </label>
                <select
                  id="mem-fam-member"
                  className="input"
                  value={activeFamilyMemberId ?? ''}
                  onChange={(e) => setActiveFamilyMemberId(e.target.value || null)}
                  disabled={familySessionMode === 'shared'}
                >
                  {familyMembers.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.family_display_name}
                      {m.relation ? ` (${m.relation})` : ''}
                      {m.user_id === family.owner_user_id ? ' ★' : ''}
                    </option>
                  ))}
                </select>
                <div className="seg" role="tablist" aria-label="Family scope">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={familySessionMode === 'private'}
                    className={familySessionMode === 'private' ? 'on' : ''}
                    onClick={() => setFamilySessionMode('private')}
                  >
                    {L2({ en: '1:1', ru: '1:1' })}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={familySessionMode === 'shared'}
                    className={familySessionMode === 'shared' ? 'on' : ''}
                    onClick={() => setFamilySessionMode('shared')}
                  >
                    {L2({ en: 'Shared only', ru: 'Только общее' })}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {/* VIEW TOGGLE — OD segmented control. `.view-seg` to avoid clobbering
            the shared `.seg` used by the family-scope picker above. */}
        <div className="view-seg" role="group" aria-label="View">
          <button aria-pressed={view === 'chains'} onClick={() => setView('chains')}>
            {t('mem.view.chains')}
          </button>
          <button aria-pressed={view === 'memories'} onClick={() => setView('memories')}>
            {t('mem.view.memories')}
          </button>
          <button aria-pressed={view === 'shares'} onClick={() => setView('shares')}>
            {t('mem.view.shares')}
          </button>
        </div>

        {/* ============ CHAINS VIEW ============ */}
        {view === 'chains' && (
          <section className="view" data-active="true" aria-label="Chains">
            <h2 className="h-sec">{t('mem.h.linked')}</h2>
            {/* Recall probe — the server exposes no "list all chains" endpoint,
                so chains surface on demand from a probe (honest, not a static
                demo list). Styled as an OD `.chain` once results arrive. */}
            <div className="card mem-probe-card">
              <div className="card-title">{t('mem.recall')}</div>
              <div className="key-row">
                <input
                  className="input"
                  value={probe}
                  onChange={(e) => setProbe(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') doRecall();
                  }}
                  placeholder={t('mem.recall.ph')}
                  aria-label={t('mem.recall')}
                />
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={doRecall}
                  disabled={busy || !probe.trim()}
                >
                  {t('mem.recall.go')}
                </button>
              </div>
              {chains !== null && (
                <div className="rc-results">
                  {chains.length === 0 ? (
                    <div className="help">{t('mem.recall.empty')}</div>
                  ) : (
                    chains.map((ch, i) => (
                      <article className="chain" key={i}>
                        <div className="chain__head">
                          <div>
                            <div className="chain__title">{t('mem.chain')}</div>
                          </div>
                          <div className="chain__meta tnum">
                            {ch.events.length} events · {t('mem.salience')}{' '}
                            {ch.salience_sum.toFixed(2)}
                          </div>
                        </div>
                        <div className="chain__events">
                          {ch.events.map((e) => (
                            <div className="chain__ev" key={e.id}>
                              <span className={`who${e.role === 'user' ? ' who--u' : ''}`}>
                                {e.role}
                              </span>
                              <span className="txt">
                                {e.content}
                                {/* Auto-classified emotional signal (Phase 1b).
                                    Presented as classifier output — tags are
                                    rendered verbatim, the bar is intensity;
                                    never a claim about anyone's feelings. */}
                                {((e.emotional_intensity ?? 0) >= 0.4 ||
                                  (e.emotion_tags?.length ?? 0) > 0) && (
                                  <span className="ev-emo" title={t('mem.emo.title')}>
                                    {(e.emotional_intensity ?? 0) >= 0.4 && (
                                      <span className="ev-emo__bar" aria-hidden>
                                        <i
                                          style={{
                                            width: `${Math.round((e.emotional_intensity ?? 0) * 100)}%`,
                                          }}
                                        />
                                      </span>
                                    )}
                                    {(e.emotion_tags ?? []).slice(0, 3).map((tag) => (
                                      <span key={tag} className="ev-emo__tag">
                                        {tag}
                                      </span>
                                    ))}
                                  </span>
                                )}
                              </span>
                            </div>
                          ))}
                        </div>
                      </article>
                    ))
                  )}
                </div>
              )}
            </div>
          </section>
        )}

        {/* ============ MEMORIES VIEW ============ */}
        {view === 'memories' && (
          <section className="view" data-active="true" aria-label="Memories">
            <h2 className="h-sec">{t('mem.h.atomic')}</h2>
            <div className="mem-themes">
              <button
                type="button"
                className={`chip${theme === null ? '' : ' neutral'}`}
                onClick={() => setTheme(null)}
              >
                {t('mem.f.all')}
              </button>
              {themes.map((tg) => (
                <button
                  type="button"
                  key={tg}
                  className={`chip${theme === tg ? '' : ' neutral'}`}
                  onClick={() => setTheme(theme === tg ? null : tg)}
                >
                  {tg}
                </button>
              ))}
            </div>
            {!loaded ? (
              <div className="help">{t('mem.loading')}</div>
            ) : visible.length === 0 ? (
              <div className="mem-empty">
                <div className="help">{t('mem.empty')}</div>
                <Link className="btn btn-primary" href="/chat">
                  {t('mem.empty.cta')}
                </Link>
              </div>
            ) : (
              <div className="mem-timeline stagger">
                {visible.map((m, i) => {
                  const turns = m.source_event_ids.length;
                  const updated = relativeUpdated(m.updated_at, t);
                  const shared = m.persona_id !== personaId;
                  const donorName = shared ? personaById(m.persona_id, personaList).name : '';
                  return (
                    <div className="mem-card" key={m.id} style={{ animationDelay: `${i * 70}ms` }}>
                      <div className="mem-head">
                        {renderStars(m.salience)}
                        {updated && (
                          <span className="mem-when">{t('mem.updated', { when: updated })}</span>
                        )}
                        {shared && (
                          <span className="tag" title={t('mem.share.from', { name: donorName })}>
                            {t('mem.share.from', { name: donorName })}
                          </span>
                        )}
                      </div>
                      <div className="mem-summary">{m.content}</div>
                      <div className="mem-foot">
                        <div className="mem-tags">
                          {m.tags.map((tg) => (
                            <span className="tag" key={tg}>
                              {tg}
                            </span>
                          ))}
                        </div>
                        <div className="salience">
                          <span className="bar">
                            <i style={{ width: `${Math.round(m.salience * 100)}%` }} />
                          </span>
                          <span className="lbl tnum">
                            {t('mem.salience')} {m.salience.toFixed(2)}
                          </span>
                        </div>
                      </div>
                      {turns > 0 && (
                        <div className="mem-turns tnum">{t('mem.drawn', { n: turns })}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        )}

        {/* ============ SHARES VIEW ============ */}
        {view === 'shares' && (
          <section className="view" data-active="true" aria-label="Shares">
            <h2 className="h-sec">{t('mem.h.shares')}</h2>
            {/* Donor-side management — the donor (selected persona) shares its
                memory INTO a receiver; the receiver recalls the donor's chains
                and lists its memories (badged "shared from {donor}"). A
                reference, not a copy: revocable, nothing duplicated. */}
            <div className="card">
              <div className="card-title">{t('mem.share.title')}</div>
              <div className="help" style={{ marginBottom: 10 }}>
                {t('mem.share.sub')}
              </div>
              <div className="key-row">
                <select
                  className="input"
                  value={sharePick}
                  onChange={(e) => {
                    setSharePick(e.target.value);
                    setShareErr(false);
                  }}
                  style={{ maxWidth: 280 }}
                  aria-label={t('mem.share.pick')}
                >
                  <option value="">{t('mem.share.pick')}</option>
                  {shareCandidates.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} · {L2({ en: p.role.en, ru: p.role.ru })}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={addShare}
                  disabled={!sharePick}
                >
                  {t('mem.share.add')}
                </button>
              </div>
              {shareErr && (
                <div className="help" style={{ marginTop: 8 }}>
                  {t('mem.share.self')}
                </div>
              )}
              {shares.length > 0 ? (
                <div className="share-list">
                  {shares.map((s) => {
                    const name = personaById(s.receiver_persona_id, personaList).name;
                    return (
                      <div className="share" key={s.id}>
                        <div className="share__body">
                          <div className="share__src">
                            <span className="from">{t('mem.share.with', { name })}</span>
                          </div>
                          <div className="share__foot">
                            <button
                              type="button"
                              className="btn btn-sm btn-ghost"
                              onClick={() => removeShare(s.receiver_persona_id)}
                              title={t('mem.share.remove')}
                            >
                              {t('mem.share.remove')}
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="help" style={{ marginTop: 12 }}>
                  {t('mem.share.empty')}
                </div>
              )}
            </div>
          </section>
        )}

        {/* ============ DANGER ZONE ============ */}
        <section className="danger" aria-label="Danger zone">
          <div className="danger__h">{t('mem.danger.h')}</div>
          <div className="danger__row">
            <div className="danger__txt">
              <div className="t">{t('mem.wipe')}</div>
              <div className="x">{t('mem.wipe.hint')}</div>
            </div>
            {!confirmingWipe ? (
              <button
                type="button"
                className="btn btn-stop btn-sm"
                onClick={() => setConfirmingWipe(true)}
                disabled={wipeBusy}
              >
                {t('mem.wipe')}
              </button>
            ) : (
              <div className="danger__confirm">
                <div className="help" style={{ flexBasis: '100%' }}>
                  {t('mem.wipe.confirm', { name: personaById(personaId, personaList).name })}
                </div>
                <button
                  type="button"
                  className="btn btn-stop btn-sm"
                  onClick={doWipe}
                  disabled={wipeBusy}
                >
                  {t('mem.wipe.confirm.yes')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => setConfirmingWipe(false)}
                  disabled={wipeBusy}
                >
                  {t('mem.wipe.confirm.no')}
                </button>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
