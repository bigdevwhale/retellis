'use client';

// Breathing + meditation practices, grouped under one "Practices" nav entry
// like Opera Air's wellness menu. Fully client-side: no LLM, no backend, no
// network — these are standalone tools, not companion turns. The breathing
// pacer is a CSS-transitioned circle driven by a phase state machine; the
// meditation timer is a countdown with a WebAudio bell (no audio asset) and an
// optional spoken intro via the browser's SpeechSynthesis (speech.ts).
//
// Tone follows the product contract: instructions tell the user what to do,
// they never have the companion "perform" calm or empathy ("disclose, don't
// perform").

import { useLang } from '@/lib/i18n';
import { useSpeech } from '@/lib/speech';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

// --- Breathing patterns -----------------------------------------------------

type PhaseKey = 'inhale' | 'hold-in' | 'exhale' | 'hold-out';
type Phase = { key: PhaseKey; secs: number };
type Pattern = { id: string; labelKey: string; descKey: string; phases: Phase[] };

const PATTERNS: Pattern[] = [
  {
    id: 'box',
    labelKey: 'pr.pattern.box',
    descKey: 'pr.pattern.box.desc',
    phases: [
      { key: 'inhale', secs: 4 },
      { key: 'hold-in', secs: 4 },
      { key: 'exhale', secs: 4 },
      { key: 'hold-out', secs: 4 },
    ],
  },
  {
    id: 'calm',
    labelKey: 'pr.pattern.calm',
    descKey: 'pr.pattern.calm.desc',
    phases: [
      { key: 'inhale', secs: 4 },
      { key: 'hold-in', secs: 4 },
      { key: 'exhale', secs: 6 },
      { key: 'hold-out', secs: 2 },
    ],
  },
  {
    id: 'relax',
    labelKey: 'pr.pattern.relax',
    descKey: 'pr.pattern.relax.desc',
    phases: [
      { key: 'inhale', secs: 4 },
      { key: 'hold-in', secs: 7 },
      { key: 'exhale', secs: 8 },
    ],
  },
];

const PHASE_LABEL: Record<PhaseKey, string> = {
  inhale: 'pr.phase.inhale',
  'hold-in': 'pr.phase.hold',
  exhale: 'pr.phase.exhale',
  'hold-out': 'pr.phase.hold',
};

// Target circle scale per phase. Inhale grows to 1; exhale shrinks to 0.55;
// holds stay where the previous phase left them. The CSS transition duration is
// set to the phase length so the circle breathes at the right tempo.
const PHASE_SCALE: Record<PhaseKey, number> = {
  inhale: 1,
  'hold-in': 1,
  exhale: 0.55,
  'hold-out': 0.55,
};

// --- Meditation themes ------------------------------------------------------

type Theme = { id: string; labelKey: string; descKey: string; cueKey: string };
const THEMES: Theme[] = [
  {
    id: 'breath',
    labelKey: 'pr.theme.breath',
    descKey: 'pr.theme.breath.desc',
    cueKey: 'pr.theme.breath.cue',
  },
  {
    id: 'body',
    labelKey: 'pr.theme.body',
    descKey: 'pr.theme.body.desc',
    cueKey: 'pr.theme.body.cue',
  },
  {
    id: 'metta',
    labelKey: 'pr.theme.metta',
    descKey: 'pr.theme.metta.desc',
    cueKey: 'pr.theme.metta.cue',
  },
];

const DURATIONS_MIN = [3, 5, 10];

// Soft bell via WebAudio — no audio asset, created lazily on first user gesture
// (the Start button) so it satisfies autoplay policies. Two tones for start/end.
function useBell() {
  const ctxRef = useRef<AudioContext | null>(null);
  return useCallback((kind: 'start' | 'end') => {
    if (typeof window === 'undefined') return;
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    if (!ctxRef.current) ctxRef.current = new Ctor();
    const ctx = ctxRef.current;
    if (ctx.state === 'suspended') void ctx.resume();
    // Start: a brighter 660Hz; end: a lower 396Hz, played twice for closure.
    const freq = kind === 'start' ? 660 : 396;
    const dur = kind === 'start' ? 1.1 : 1.6;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = 'sine';
    o.frequency.value = freq;
    const t0 = ctx.currentTime;
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(0.16, t0 + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g);
    g.connect(ctx.destination);
    o.start(t0);
    o.stop(t0 + dur + 0.05);
  }, []);
}

function BreathingPacer() {
  const { t } = useLang();
  const [patternId, setPatternId] = useState('box');
  const [running, setRunning] = useState(false);
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [remaining, setRemaining] = useState(4);
  const [cycles, setCycles] = useState(0);

  const pattern = PATTERNS.find((p) => p.id === patternId) ?? PATTERNS[0]!;
  const phases = pattern.phases;
  const phase = phases[phaseIdx]!;

  // Refs mirror the timer's working state so the 100ms interval never reads a
  // stale phaseIdx / phaseStart (the closure captures these, not React state).
  const phaseIdxRef = useRef(0);
  const phaseStartRef = useRef(0);

  const resetTo = useCallback(
    (idx: number) => {
      phaseIdxRef.current = idx;
      phaseStartRef.current = Date.now();
      setPhaseIdx(idx);
      setRemaining(phases[idx]!.secs);
    },
    [phases],
  );

  const start = () => {
    setCycles(0);
    resetTo(0);
    setRunning(true);
  };
  const stop = () => {
    setRunning(false);
    setCycles(0);
    resetTo(0);
  };

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      const now = Date.now();
      const elapsed = now - phaseStartRef.current;
      const cur = phases[phaseIdxRef.current]!;
      const durMs = cur.secs * 1000;
      if (elapsed >= durMs) {
        const next = (phaseIdxRef.current + 1) % phases.length;
        if (next === 0) setCycles((c) => c + 1);
        phaseIdxRef.current = next;
        phaseStartRef.current = now;
        setPhaseIdx(next);
        setRemaining(phases[next]!.secs);
      } else {
        setRemaining(Math.ceil((durMs - elapsed) / 1000));
      }
    }, 100);
    return () => window.clearInterval(id);
  }, [running, phases]);

  // Switching pattern mid-session resets — patterns have different phase counts
  // and tempos, so continuing across the switch would be incoherent. Depends on
  // patternId only: adding `running`/`resetTo` would re-fire when those change
  // (e.g. starting would immediately reset).
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional pattern-change reset
  useEffect(() => {
    if (running) setRunning(false);
    resetTo(0);
    setCycles(0);
  }, [patternId]);

  const targetScale = running ? PHASE_SCALE[phase.key] : 0.55;
  const transitionSecs = running ? phase.secs : 0.6;

  return (
    <div className="pr-panel breathe-stage">
      <div className="pr-intro">{t('pr.breathing.intro')}</div>

      <div className="breathe-orb">
        <span className="breathe-orb__halo" />
        <div
          className="breathe-orb__ring"
          style={{
            transform: `scale(${targetScale})`,
            transition: `transform ${transitionSecs}s linear`,
          }}
        >
          <span className="breathe-orb__ring breathe-orb__ring--inner" />
        </div>
        <div className="breathe-orb__label">
          <div className="breathe-orb__phase">{running ? t(PHASE_LABEL[phase.key]) : ''}</div>
          <div className="breathe-orb__count">
            <span className="tnum">{running ? remaining : ''}</span>
            {running && (
              <span className="total">
                {' / '}
                <span className="tnum">{phase.secs}</span>
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Phase row — the current pattern's phases, with the active one marked. */}
      <div className="phase-row">
        {phases.map((ph, i) => (
          <span
            key={ph.key}
            data-phase={i}
            data-phase-active={running && i === phaseIdx ? '1' : undefined}
          >
            {t(PHASE_LABEL[ph.key])} <span className="tnum">{ph.secs}</span>
          </span>
        ))}
      </div>

      {/* Pattern selector — OD ``.pattern-row``. The 3 real patterns as a seg;
          the OD ``Custom`` builder is not ported (would be new logic, out of scope
          for a layout port). */}
      <div className="pattern-row">
        <div className="seg" role="group" aria-label={t('pr.tab.breathing')}>
          {PATTERNS.map((p) => (
            <button
              key={p.id}
              type="button"
              aria-pressed={p.id === patternId}
              className={p.id === patternId ? 'on' : ''}
              onClick={() => setPatternId(p.id)}
              title={t(p.descKey)}
            >
              {t(p.labelKey)}
            </button>
          ))}
        </div>
      </div>

      <div className="session-meta">
        <span>{t('pr.cycles.label')}</span>
        <span className="tnum">{cycles}</span>
      </div>

      <div className="practice-actions">
        {!running ? (
          <button type="button" className="btn btn-primary" onClick={start}>
            {t('pr.start')}
          </button>
        ) : (
          <button type="button" className="btn" onClick={stop}>
            {t('pr.stop')}
          </button>
        )}
      </div>
    </div>
  );
}

function MeditationTimer() {
  const { t, lang } = useLang();
  const bell = useBell();
  const { speak } = useSpeech(lang);
  // speechSynthesis (TTS) is the only capability the "Speak the intro"
  // control needs; useSpeech.supported would also require SpeechRecognition,
  // which is too strict for a speak-only button, so we gate on TTS alone.
  const [tts, setTts] = useState(false);
  useEffect(() => {
    setTts(typeof window !== 'undefined' && 'speechSynthesis' in window);
  }, []);

  const [durationMin, setDurationMin] = useState(5);
  const [themeId, setThemeId] = useState('breath');

  // Three states: idle, running, paused. End-of-timer transitions
  // running → idle with `done` set; Pause/Resume toggles running ⇄ paused
  // without resetting `remainingMs`. Stop always returns to idle.
  const [running, setRunning] = useState(false);
  const [paused, setPaused] = useState(false);
  const [remainingMs, setRemainingMs] = useState(5 * 60_000);
  const [done, setDone] = useState(false);

  const theme = THEMES.find((x) => x.id === themeId) ?? THEMES[0]!;
  const totalMs = durationMin * 60_000;

  const lastTickRef = useRef(0);
  const remainingRef = useRef(remainingMs);
  remainingRef.current = remainingMs;

  const finishSession = useCallback(() => {
    setRunning(false);
    setPaused(false);
    setDone(true);
    bell('end');
  }, [bell]);

  const start = () => {
    // Fresh start: reset clock, fire intro bell, mark running.
    setDone(false);
    setRemainingMs(totalMs);
    remainingRef.current = totalMs;
    lastTickRef.current = Date.now();
    setPaused(false);
    setRunning(true);
    bell('start');
  };

  const resume = () => {
    if (!paused) return;
    // Resume without resetting the clock: only re-seat the tick anchor so
    // the next interval delta is computed from "now", not from the pause
    // moment (which would otherwise drain the session while paused).
    lastTickRef.current = Date.now();
    setPaused(false);
    setRunning(true);
  };

  const pause = () => {
    if (!running) return;
    setRunning(false);
    setPaused(true);
  };

  const stop = () => {
    // Hard reset: returns to idle, full ring, no done label.
    setRunning(false);
    setPaused(false);
    setRemainingMs(totalMs);
    setDone(false);
  };

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      const now = Date.now();
      const delta = now - lastTickRef.current;
      lastTickRef.current = now;
      const next = remainingRef.current - delta;
      if (next <= 0) {
        setRemainingMs(0);
        finishSession();
      } else {
        setRemainingMs(next);
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [running, finishSession]);

  // Changing duration while idle just updates the display; while running or
  // paused, treat it as a reset (mixing durations mid-run is incoherent).
  // Depends on durationMin only: adding `running`/`paused` would re-fire on
  // pause toggles and clobber a live session.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional duration-change reset
  useEffect(() => {
    if (running || paused) stop();
    else setRemainingMs(durationMin * 60_000);
  }, [durationMin]);

  const mm = Math.floor(remainingMs / 60_000);
  const ss = Math.floor((remainingMs % 60_000) / 1000);
  const clock = `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;

  // SVG progress ring: a depleting arc. r=84 → C = 2π·84 ≈ 527.79.
  const R = 84;
  const C = 2 * Math.PI * R;
  const frac = totalMs > 0 ? remainingMs / totalMs : 0;
  const offset = C * (1 - frac);

  return (
    <div className="pr-panel med-stage">
      <div className="pr-intro">{t('pr.meditation.intro')}</div>

      {/* Duration — three compact cards in the same visual grammar as the
          theme cards, with a "min" suffix in --label. */}
      <fieldset className="pr-chips-duration" aria-label={t('pr.duration')}>
        {DURATIONS_MIN.map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={d === durationMin}
            className={`pr-chip${d === durationMin ? ' is-active' : ''}`}
            onClick={() => setDurationMin(d)}
          >
            <span className="tnum">{d}</span>
            <span className="pr-chips-duration__unit">{t('pr.min')}</span>
          </button>
        ))}
      </fieldset>

      <div className="pr-field-label">{t('pr.theme')}</div>
      <div className="pr-chips-grid" aria-label={t('pr.theme')}>
        {THEMES.map((th) => (
          <button
            key={th.id}
            type="button"
            aria-pressed={th.id === themeId}
            className={`pr-chip${th.id === themeId ? ' is-active' : ''}`}
            onClick={() => setThemeId(th.id)}
          >
            <span className="pr-chip-name">{t(th.labelKey)}</span>
            <span className="pr-chip-desc">{t(th.descKey)}</span>
          </button>
        ))}
      </div>

      <div
        className="med-ring-wrap"
        data-running={running ? '1' : undefined}
        data-done={done ? '1' : undefined}
      >
        <svg
          className="med-ring"
          viewBox="0 0 200 200"
          width={220}
          height={220}
          role="img"
          aria-label={t('pr.tab.meditation')}
        >
          <title>{t('pr.tab.meditation')}</title>
          <circle className="med-ring-bg" cx={100} cy={100} r={R} />
          <circle
            className="med-ring-fg"
            cx={100}
            cy={100}
            r={R}
            strokeDasharray={C}
            strokeDashoffset={offset}
            transform="rotate(-90 100 100)"
          />
          {/* Minute ticks — 12 marks, one per 5 minutes of an hour clock. They
              are static (the foreground arc is the real progress) and just
              give the ring a "this is a clock" silhouette. */}
          {Array.from({ length: 12 }).map((_, i) => {
            const a = (i / 12) * Math.PI * 2 - Math.PI / 2;
            const r1 = 92;
            const r2 = i % 3 === 0 ? 88 : 90;
            return (
              <line
                key={i}
                className="med-ring-tick"
                x1={100 + r1 * Math.cos(a)}
                y1={100 + r1 * Math.sin(a)}
                x2={100 + r2 * Math.cos(a)}
                y2={100 + r2 * Math.sin(a)}
              />
            );
          })}
        </svg>
        <div className="med-ring-center">
          {done ? (
            <svg
              className="med-ring-check"
              viewBox="0 0 48 48"
              width={56}
              height={56}
              role="img"
              aria-label={t('pr.done')}
            >
              <path
                d="M12 25 L21 34 L37 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : (
            <div className="med-ring-clock tnum">{clock}</div>
          )}
          <div className="med-ring-sub">
            {done ? t('pr.done') : paused ? t('pr.pause') : t('pr.remaining')}
          </div>
        </div>
      </div>

      <div className="pr-instruction">{t(theme.cueKey)}</div>

      <div className="practice-actions">
        {tts && (
          <button
            type="button"
            className="btn"
            onClick={() => speak(t(theme.cueKey))}
            disabled={running}
          >
            {t('pr.speak.cues')}
          </button>
        )}
        {running ? (
          <button type="button" className="btn" onClick={pause} aria-label={t('pr.pause')}>
            {t('pr.pause')}
          </button>
        ) : paused ? (
          <button
            type="button"
            className="btn btn-primary"
            onClick={resume}
            aria-label={t('pr.resume')}
          >
            {t('pr.resume')}
          </button>
        ) : null}
        {!running && !paused ? (
          <button type="button" className="btn btn-primary" onClick={start}>
            {done ? t('pr.again') : t('pr.start')}
          </button>
        ) : (
          <button type="button" className="btn" onClick={stop}>
            {t('pr.stop')}
          </button>
        )}
      </div>
    </div>
  );
}

export function PracticesScreen() {
  const { t } = useLang();
  const router = useRouter();
  const search = useSearchParams();
  // `useSearchParams()` returns `null` on the very first client render before
  // the router state hydrates, and Next 15's static prerender for `/practices`
  // also bakes in the `breathing` default. Reading it eagerly here would make
  // `?tab=meditation` produce a different tree on the server (BreathingPacer)
  // vs the client (MeditationTimer) → React error #418 → the `<Rail>` and
  // surrounding shell get unmounted mid-hydration, which on mobile presents
  // as "a side menu opened, the bottom nav disappeared, content is gone."
  // Pin the first paint to the default and flip after mount.
  const [tab, setTabState] = useState<'breathing' | 'meditation'>('breathing');
  useEffect(() => {
    const next = search.get('tab') === 'meditation' ? 'meditation' : 'breathing';
    setTabState(next);
  }, [search]);

  const setTab = (next: 'breathing' | 'meditation') => {
    setTabState(next);
    router.push(`/practices?tab=${next}`);
  };

  return (
    <main className="practices-layout">
      <div className="pagehead">
        <div className="pagehead__row">
          <div>
            <h1>{t('pr.title')}</h1>
            <p className="lede">{t('pr.subtitle')}</p>
          </div>
          <span className="pr-badge-offline">
            <span className="dot" aria-hidden="true" />
            {t('pr.offline')}
          </span>
        </div>
      </div>

      <div
        className="seg"
        role="tablist"
        aria-label={t('pr.title')}
        style={{ margin: '0 auto 10px' }}
      >
        <button
          type="button"
          aria-pressed={tab === 'breathing'}
          className={tab === 'breathing' ? 'on' : ''}
          onClick={() => setTab('breathing')}
        >
          {t('pr.tab.breathing')}
        </button>
        <button
          type="button"
          aria-pressed={tab === 'meditation'}
          className={tab === 'meditation' ? 'on' : ''}
          onClick={() => setTab('meditation')}
        >
          {t('pr.tab.meditation')}
        </button>
      </div>
      <div className="card pr-card">
        {tab === 'breathing' ? <BreathingPacer /> : <MeditationTimer />}
      </div>

      <p className="limit-line">{t('pr.limit')}</p>
    </main>
  );
}
