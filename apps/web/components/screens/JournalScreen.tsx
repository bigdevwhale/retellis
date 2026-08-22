'use client';

import {
  type JournalEntryRecord,
  createJournalEntry,
  deleteJournalEntry,
  listJournalEntries,
  listJournalTags,
  updateJournalEntry,
} from '@/lib/api-client';
import { useLang } from '@/lib/i18n';
import { useStore } from '@/lib/store';
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

// "Matters to me" maps a 1..3 user choice to salience 0.33 / 0.66 / 1.0. Level 0
// (none selected) is salience 0. The journal never judges salience — it is the
// user's "this matters to me" dial, not an LLM score ("disclose, don't perform").
const MATTERS_TO_SALIENCE = [0, 0.33, 0.66, 1.0] as const;
const SALIENCE_TO_LEVEL = (s: number): 0 | 1 | 2 | 3 => {
  if (s <= 0) return 0;
  if (s >= 0.9) return 3;
  if (s >= 0.5) return 2;
  return 1;
};

// Group entries by the calendar day the user experienced (local time, not UTC),
// so "Today" / "Yesterday" line up with how the user thinks about their day.
function dayKey(iso: string): string;
function dayKey(d: Date): string;
function dayKey(x: string | Date): string {
  const d = typeof x === 'string' ? new Date(x) : x;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

type Draft = {
  title: string;
  body: string;
  mood: string;
  tags: string[];
  matters: 0 | 1 | 2 | 3;
  // Provenance: which chat conversation/event this entry was seeded from.
  // Preserved until save so source_convo_id/source_event_id are persisted correctly.
  convoId: string | null;
  eventId: string | null;
};

const EMPTY_DRAFT: Draft = { title: '', body: '', mood: '', tags: [], matters: 0, convoId: null, eventId: null };

// The journal is persona-agnostic by design — a quiet, common diary surface,
// not per-companion. The row's NOT-NULL ``persona_id`` still needs a value, so
// all entries are filed under ``lou`` (the built-in "Journaler / Дневник"
// persona). ``source_convo_id`` carries the real provenance when an entry was
// seeded from a chat message, without tying it to a companion.
const JOURNAL_PERSONA = 'lou';

// Mood quick-picker — each chip writes a localized word into the same free-text
// ``mood`` field the API already stores. No new field, no taxonomy imposed on
// past entries; the picker is just a gentle shortcut for the author.
const MOODS = [
  { emoji: '🌿', lk: 'journal.mood.calm', color: 'var(--mint)' },
  { emoji: '☺️', lk: 'journal.mood.joy', color: 'var(--amber)' },
  { emoji: '🙏', lk: 'journal.mood.grateful', color: 'var(--mint)' },
  { emoji: '✨', lk: 'journal.mood.hopeful', color: 'var(--purple)' },
  { emoji: '🌙', lk: 'journal.mood.tired', color: 'var(--body)' },
  { emoji: '🌊', lk: 'journal.mood.anxious', color: 'var(--magenta)' },
  { emoji: '🌧️', lk: 'journal.mood.sad', color: 'var(--coral)' },
  { emoji: '⛅️', lk: 'journal.mood.neutral', color: 'var(--body)' },
] as const;

// Map a stored mood word (en or ru) to a mood-token name. The token resolves
// to a real color in :root and [data-theme="light"] (see globals.css), so CSS
// can use it inside color-mix() to derive the orb's 3D shading. Free-text
// moods the user typed by hand fall through to --mood-hopeful — the journal
// never invents affect for words it doesn't recognise ("disclose, don't
// perform"). The token name (not the var(--…) string) is what's returned, so
// it can be composed: var(--jorb) → var(--mood-joy) → #d4a23a.
const MOOD_COLORS: Record<string, string> = {
  // en
  calm: '--mood-calm',
  joy: '--mood-joy',
  grateful: '--mood-grateful',
  hopeful: '--mood-hopeful',
  tired: '--mood-tired',
  anxious: '--mood-anxious',
  sad: '--mood-sad',
  neutral: '--mood-neutral',
  // ru
  спокойно: '--mood-calm',
  радость: '--mood-joy',
  благодарность: '--mood-grateful',
  надежда: '--mood-hopeful',
  усталость: '--mood-tired',
  тревога: '--mood-anxious',
  грусть: '--mood-sad',
  ровно: '--mood-neutral',
};

function moodColor(mood: string | null | undefined): string {
  if (!mood) return 'var(--mood-hopeful)';
  const k = mood.trim().toLowerCase();
  const token = MOOD_COLORS[k] ?? '--mood-hopeful';
  // Wrap the token name in var() so `color-mix(in srgb, var(--jorb) ...)`
  // sees a <color> on both sides of the mix.
  return `var(${token})`;
}

const PROMPTS = ['journal.prompt.joy', 'journal.prompt.remember', 'journal.prompt.now'] as const;

function dayOfYear(d: Date): number {
  const start = new Date(d.getFullYear(), 0, 0);
  return Math.floor((d.getTime() - start.getTime()) / 86_400_000);
}

function entryDateLabel(iso: string, locale: string): string {
  const d = new Date(iso);
  const datePart = d.toLocaleDateString(locale, { day: 'numeric', month: 'short' });
  const timePart = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
  return `${datePart} · ${timePart}`;
}

// --- Best-effort draft persistence (writer only, never while editing) -------
// A debounced snapshot of the in-progress entry to localStorage, so a refresh
// or accidental close doesn't lose a half-written page. Restored only when the
// writer opens fresh (no seed, no prompt, no edit). Cleared on save. This is a
// client convenience — the server never sees the draft.
const DRAFT_KEY = 'retellis.journal.draft';

function loadDraft(): Draft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw) as Partial<Draft>;
    return {
      title: typeof d.title === 'string' ? d.title : '',
      body: typeof d.body === 'string' ? d.body : '',
      mood: typeof d.mood === 'string' ? d.mood : '',
      tags: Array.isArray(d.tags) ? (d.tags as string[]) : [],
      matters: d.matters === 1 || d.matters === 2 || d.matters === 3 ? d.matters : 0,
      convoId: typeof d.convoId === 'string' ? d.convoId : null,
      eventId: typeof d.eventId === 'string' ? d.eventId : null,
    };
  } catch {
    return null;
  }
}
function saveDraft(d: Draft): void {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
  } catch {
    /* private mode / quota — best-effort, surface nothing */
  }
}
function clearDraft(): void {
  try {
    localStorage.removeItem(DRAFT_KEY);
  } catch {
    /* ignore */
  }
}

// Entry body — serif, reading-width, clamped with a soft mask fade when it
// overflows, with a "Read in full" / "Show less" toggle. Overflow is detected
// by measuring scrollHeight against the clamped clientHeight after paint, so
// the toggle only appears for bodies that actually need it.
function EntryBody({
  body,
  expanded,
  onToggle,
  t,
}: {
  body: string;
  expanded: boolean;
  onToggle: () => void;
  t: (k: string, v?: Record<string, string | number>) => string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);

  // The clamp toggle should only appear for bodies that overflow it. We detect
  // that by measuring scrollHeight vs. the clamped clientHeight after paint.
  // `body` drives the rendered text but isn't read directly here, so biome's
  // exhaustive-deps heuristic flags it — keep it: a remount or an edited body
  // (same key, new text) must re-measure.
  // biome-ignore lint/correctness/useExhaustiveDependencies: body drives the DOM text; re-measure on change.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setOverflow(el.scrollHeight > el.clientHeight + 8);
  }, [body]);

  const clamped = overflow && !expanded;
  return (
    <>
      <div ref={ref} className={`jbody${clamped ? ' clamped' : ''}`}>
        {body}
      </div>
      {overflow && (
        <button type="button" className="jreadfull" aria-expanded={expanded} onClick={onToggle}>
          {expanded ? t('journal.showless') : t('journal.readfull')}
        </button>
      )}
    </>
  );
}

export function JournalScreen() {
  const { t, lang } = useLang();
  const dateLocale = lang === 'ru' ? 'ru-RU' : 'en-US';
  const journalSeed = useStore((s) => s.journalSeed);
  const setJournalSeed = useStore((s) => s.setJournalSeed);

  const [entries, setEntries] = useState<JournalEntryRecord[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [errored, setErrored] = useState(false);

  // Two view-states on one screen: ``home`` (the warm diary home) and ``write``
  // (a focused writing moment). No routing — the transition is a soft cross-fade.
  const [mode, setMode] = useState<'home' | 'write'>('home');
  // A soft prompt the user picked, shown as a faded guide above the writer. It
  // is never saved — it's a nudge, not content.
  const [promptHint, setPromptHint] = useState('');

  // Filters — search is applied on Enter (keystroke-level API hits would be
  // noisy); the mood filter applies immediately. ``q`` is the live input,
  // ``qApplied`` is what was actually sent.
  const [q, setQ] = useState('');
  const [qApplied, setQApplied] = useState('');
  const [moodFilter, setMoodFilter] = useState<string>('');
  // Tag filter (OD side-search tag cloud). Single-select, like the mood filter;
  // backed by the same ``tag`` query param the API already supports (JSONB @>).
  const [tagFilter, setTagFilter] = useState<string>('');
  // Mobile collapsible side-search (OD ``.search-toggle``).
  const [searchOpen, setSearchOpen] = useState(false);

  // Composer draft + the entry currently being edited (null = "create" mode).
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [editing, setEditing] = useState<JournalEntryRecord | null>(null);
  const [tagInput, setTagInput] = useState('');
  const [busy, setBusy] = useState(false);

  // Per-entry "Read in full" expansion + inline delete confirm.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  // Roving focus across the month ribbon (arrow keys move between enabled days).
  const [ribbonFocusKey, setRibbonFocusKey] = useState<string | null>(null);

  // Fading "Draft saved" hint in the writer foot.
  const [draftHint, setDraftHint] = useState<string | null>(null);

  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const ribbonRef = useRef<HTMLDivElement>(null);
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Mirror draft into a ref so the debounced saver reads fresh state without
  // rescheduling on every keystroke.
  const draftRef = useRef(draft);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  // Observed moods across loaded entries → datalist options + the mood filter
  // pills. Derived from what the user has actually authored, not a taxonomy.
  const observedMoods = useMemo(
    () => [...new Set(entries.map((e) => e.mood).filter(Boolean))] as string[],
    [entries],
  );
  // Tag cloud for the sidebar — a separate fetch (``listJournalTags``) so it
  // does NOT collapse to the active tag filter. The cloud re-fetches when the
  // user changes the mood filter (a mood-scoped cloud matches the entries on
  // screen) and after save/delete (new tags appear, stale ones disappear). It
  // deliberately ignores ``tagFilter`` so picking a chip keeps the other chips
  // visible. Exposed as ``refreshTagCloud`` so mutations can invalidate.
  const [allTags, setAllTags] = useState<string[]>([]);
  const refreshTagCloud = useCallback(async (mood?: string) => {
    try {
      const tags = await listJournalTags({ mood: mood || undefined });
      setAllTags(tags);
    } catch {
      setAllTags([]);
    }
  }, []);
  useEffect(() => {
    refreshTagCloud(moodFilter);
  }, [moodFilter, refreshTagCloud]);

  const refresh = useCallback(async () => {
    try {
      const rows = await listJournalEntries({
        q: qApplied || undefined,
        mood: moodFilter || undefined,
        tag: tagFilter || undefined,
        limit: 200,
      });
      setEntries(rows);
      setErrored(false);
    } catch {
      setEntries([]);
      setErrored(true);
    } finally {
      setLoaded(true);
    }
  }, [qApplied, moodFilter, tagFilter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Consume a chat seed on mount: open the writer with the seed as the body.
  // The seed is transient store state, cleared once consumed. Its ``convoId`` is
  // filed on the created row as ``source_convo_id`` (provenance), but no
  // companion is tied to the entry.
  useEffect(() => {
    if (!journalSeed) return;
    openWriter({ seed: journalSeed });
    setJournalSeed(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [journalSeed, setJournalSeed]);

  const autoGrow = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  const queueDraft = useCallback(() => {
    // Never persist while editing an existing entry — the draft slot is for
    // new pages only, so it can't leak into someone else's row.
    if (editing) return;
    if (draftTimer.current) clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      saveDraft(draftRef.current);
      const time = new Date().toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
      });
      setDraftHint(`${t('journal.draft.saved')} · ${time}`);
      if (hintTimer.current) clearTimeout(hintTimer.current);
      hintTimer.current = setTimeout(() => setDraftHint(null), 2200);
    }, 700);
  }, [editing, t]);

  useEffect(() => {
    return () => {
      if (draftTimer.current) clearTimeout(draftTimer.current);
      if (hintTimer.current) clearTimeout(hintTimer.current);
    };
  }, []);

  const addTag = () => {
    const tg = tagInput.trim().toLowerCase();
    if (tg && !draft.tags.includes(tg)) {
      setDraft((d) => ({ ...d, tags: [...d.tags, tg] }));
      queueDraft();
    }
    setTagInput('');
  };

  const resetComposer = () => {
    setDraft(EMPTY_DRAFT);
    setEditing(null);
    setTagInput('');
    setPromptHint('');
    setDraftHint(null);
  };

  // Open the writer. Unified path for: a brand-new page (optionally guided by a
  // soft prompt), a chat seed, or editing an existing entry. A stashed draft is
  // restored only for a fresh, unguided new page.
  const openWriter = useCallback(
    (
      opts: {
        seed?: { text: string; convoId: string; eventId: string | null; personaId: string };
        entry?: JournalEntryRecord;
        prompt?: string;
      } = {},
    ) => {
      setEditing(opts.entry ?? null);
      setTagInput('');
      setPromptHint(opts.prompt ?? '');
      setDraftHint(null);
      if (opts.entry) {
        setDraft({
          title: opts.entry.title ?? '',
          body: opts.entry.body,
          mood: opts.entry.mood ?? '',
          tags: [...opts.entry.tags],
          matters: SALIENCE_TO_LEVEL(opts.entry.salience),
          convoId: opts.entry.source_convo_id,
          eventId: opts.entry.source_event_id,
        });
      } else if (opts.seed) {
        setDraft({
          ...EMPTY_DRAFT,
          body: opts.seed.text,
          convoId: opts.seed.convoId,
          eventId: opts.seed.eventId,
        });
      } else if (opts.prompt) {
        setDraft(EMPTY_DRAFT);
      } else {
        const d = loadDraft();
        setDraft(d ? { ...d } : EMPTY_DRAFT);
      }
      setMode('write');
      requestAnimationFrame(() => {
        autoGrow();
        bodyRef.current?.focus();
      });
    },
    [autoGrow],
  );

  const beginEdit = (e: JournalEntryRecord) => openWriter({ entry: e });

  const save = async () => {
    const body = draft.body.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      if (editing) {
        const updated = await updateJournalEntry(editing.id, {
          title: draft.title.trim() || null,
          body,
          mood: draft.mood.trim() || null,
          tags: draft.tags,
        });
        // salience is not patchable here — it stays as authored.
        setEntries((prev) =>
          prev.map((e) => (e.id === editing.id ? { ...updated, salience: e.salience } : e)),
        );
      } else {
        const created = await createJournalEntry({
          persona_id: JOURNAL_PERSONA,
          body,
          title: draft.title.trim() || null,
          mood: draft.mood.trim() || null,
          tags: draft.tags,
          salience: MATTERS_TO_SALIENCE[draft.matters],
          source_convo_id: draft.convoId,
          source_event_id: draft.eventId,
        });
        setEntries((prev) => [created, ...prev]);
        clearDraft();
      }
      resetComposer();
      setMode('home');
      // Invalidate the sidebar tag cloud so newly-authored tags appear (or
      // stale ones disappear on delete) without waiting for the next mood
      // change.
      void refreshTagCloud(moodFilter);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (id: string) => {
    try {
      await deleteJournalEntry(id);
      setEntries((prev) => prev.filter((e) => e.id !== id));
      setExpanded((prev) => {
        if (!prev.has(id)) return prev;
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      if (editing?.id === id) {
        resetComposer();
        setMode('home');
      }
      // Tag cloud may now include a tag that no surviving entry carries —
      // re-fetch so the sidebar doesn't keep showing a dead chip.
      void refreshTagCloud(moodFilter);
    } catch {
      // best-effort — the next filter change refreshes; surface nothing scary.
    } finally {
      setConfirmingDelete(null);
    }
  };

  const clearFilters = () => {
    setQ('');
    setQApplied('');
    setMoodFilter('');
    setTagFilter('');
  };
  const hasFilters = Boolean(qApplied || moodFilter || tagFilter);
  const onTagPill = (tag: string) => {
    setTagFilter((cur) => (cur === tag ? '' : tag));
  };

  // Diary-home derivations — all honest, from the loaded entries.
  const todayKey = dayKey(new Date());
  const todayEntry = useMemo(
    () => entries.find((e) => dayKey(e.created_at) === todayKey) ?? null,
    [entries, todayKey],
  );
  const weekAgo = Date.now() - 7 * 86_400_000;
  const weekDays = useMemo(() => {
    const s = new Set<string>();
    for (const e of entries) {
      if (new Date(e.created_at).getTime() >= weekAgo) s.add(dayKey(e.created_at));
    }
    return s.size;
  }, [entries, weekAgo]);
  // Most-recent entry per day (entries are newest-first, so first seen wins).
  const byDay = useMemo(() => {
    const m = new Map<string, JournalEntryRecord>();
    for (const e of entries) {
      const k = dayKey(e.created_at);
      if (!m.has(k)) m.set(k, e);
    }
    return m;
  }, [entries]);

  // The current calendar month as a ribbon — one button per day, derived from
  // loaded entries (no new API). Today is ringed; future days are disabled.
  const monthDays = useMemo(() => {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth();
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const startOfToday = new Date(y, m, now.getDate()).getTime();
    return Array.from({ length: daysInMonth }, (_, i) => {
      const date = new Date(y, m, i + 1);
      const key = dayKey(date);
      const entry = byDay.get(key);
      return {
        key,
        date,
        hasEntry: Boolean(entry),
        mood: entry?.mood ?? null,
        isToday: key === todayKey,
        future: date.getTime() > startOfToday,
      };
    });
  }, [byDay, todayKey]);
  const monthName = useMemo(
    () => new Date().toLocaleDateString(undefined, { month: 'long', year: 'numeric' }),
    [],
  );
  const monthEntryDays = useMemo(() => monthDays.filter((d) => d.hasEntry).length, [monthDays]);

  // Auto-center today in the ribbon once it mounts, and seed roving focus there.
  useEffect(() => {
    setRibbonFocusKey((cur) => cur ?? todayKey);
    const el = ribbonRef.current?.querySelector(`[data-daykey="${todayKey}"]`);
    if (el) {
      const host = ribbonRef.current;
      if (host)
        host.scrollLeft = Math.max(0, (el as HTMLElement).offsetLeft - host.clientWidth / 2 + 22);
    }
  }, [todayKey]);

  const onRibbonKey = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const enabled = monthDays.filter((d) => !d.future);
    const i = enabled.findIndex((d) => d.key === ribbonFocusKey);
    if (i < 0) return;
    e.preventDefault();
    const next = enabled[i + (e.key === 'ArrowRight' ? 1 : -1)];
    if (!next) return;
    setRibbonFocusKey(next.key);
    const el = ribbonRef.current?.querySelector(`[data-daykey="${next.key}"]`);
    (el as HTMLElement | null)?.focus();
  };

  const onRibbonDay = (d: { key: string; hasEntry: boolean; isToday: boolean }) => {
    if (d.hasEntry) {
      document
        .querySelector(`article.gentry[data-daykey="${d.key}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else if (d.isToday) {
      openWriter();
    }
  };

  const onMoodPill = (mood: string) => {
    setMoodFilter((cur) => (cur === mood ? '' : mood));
  };

  const toggleExpand = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const quoteKey = `journal.quote.${dayOfYear(new Date()) % 7}`;
  const greetingKey = useMemo(() => {
    const h = new Date().getHours();
    return h < 12
      ? 'journal.greeting.morning'
      : h < 18
        ? 'journal.greeting.day'
        : 'journal.greeting.evening';
  }, []);
  const heroDate = useMemo(
    () =>
      new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }),
    [],
  );
  const writeDate = heroDate;

  const canSave = draft.body.trim().length > 0 && !busy;
  const moodDraftLc = draft.mood.trim().toLowerCase();

  // The side-search body — shared by the desktop sticky aside and the mobile
  // collapsible aside. Lives inside the component so it closes over the filter
  // state + handlers without prop-drilling.
  const renderSideSearch = () => (
    <>
      <label className="ss-field">
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.2-3.2" />
        </svg>
        <input
          type="search"
          className="ss-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') setQApplied(q.trim());
            if (e.key === 'Escape') {
              setQ('');
              setQApplied('');
            }
          }}
          placeholder={t('journal.search.ph')}
          aria-label={t('journal.search.ph')}
        />
      </label>

      <div className="ss-group">
        <span className="ss-lbl">{t('journal.tags')}</span>
        <div className="tag-cloud">
          {allTags.length === 0 ? (
            <span className="ss-empty">{t('journal.timeline.empty.month')}</span>
          ) : (
            allTags.map((tg) => (
              <button
                key={tg}
                type="button"
                className={`chip${tagFilter === tg ? ' chip--on' : ''}`}
                aria-pressed={tagFilter === tg}
                onClick={() => onTagPill(tg)}
              >
                {tg}
              </button>
            ))
          )}
        </div>
      </div>

      <div className="ss-group">
        <span className="ss-lbl">{t('journal.mood')}</span>
        <div className="mood-filter">
          {observedMoods.length === 0 ? (
            <span className="ss-empty">{t('journal.feeling.ask')}</span>
          ) : (
            observedMoods.map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={moodFilter === m}
                onClick={() => onMoodPill(m)}
                style={{ ['--mf' as string]: moodColor(m) } as CSSProperties}
              >
                <span className="swatch" aria-hidden="true" />
                {m}
              </button>
            ))
          )}
        </div>
      </div>

      {hasFilters && (
        <button type="button" className="ss-clear" onClick={clearFilters}>
          {t('journal.filter.clear')}
        </button>
      )}
    </>
  );

  return (
    <div className="journal-layout">
      {/* Side search — desktop sticky. The same body renders in the mobile
          collapsible aside below. Search applies on Enter; tag + mood filters
          are single-select and apply immediately. All options are derived from
          the entries the user has actually authored — no fixed taxonomy. */}
      <aside className="side-search" aria-label={t('journal.search.title')}>
        {renderSideSearch()}
      </aside>

      {/* Mobile search toggle + collapsible side search. */}
      <button
        type="button"
        className="search-toggle"
        aria-expanded={searchOpen}
        data-open={searchOpen ? 'true' : 'false'}
        onClick={() => setSearchOpen((v) => !v)}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.2-3.2" />
        </svg>
        <span>{t('journal.search.toggle')}</span>
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          style={{ marginLeft: 'auto' }}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      <aside
        className="side-search side-search--mobile"
        aria-label={t('journal.search.title')}
        data-open={searchOpen ? 'true' : 'false'}
      >
        {renderSideSearch()}
      </aside>

      <main>
        <div className="wrap jwrap">
          <div className="pagehead pagehead--journal">
            <div className="pagehead__row">
              <div>
                <h1>{t('journal.title')}</h1>
                <p className="lede">{t('journal.sub')}</p>
                <span className="pagehead__date">{heroDate}</span>
              </div>
              <span className="jprivacy">
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.6}
                >
                  <rect x="5" y="11" width="14" height="9" rx="2.5" />
                  <path d="M8 11V8a4 4 0 0 1 8 0v3" />
                </svg>
                {t('journal.privacy')}
              </span>
            </div>
          </div>

          {mode === 'home' ? (
            <div className="jhome" key="home">
              {/* Hero — greeting, the big CTA, a factual week note, and the mood
                orb colored by today's authored mood. (Date + lede moved to the
                OD pagehead above; the duplicate eyebrow/hero-line were dropped.) */}
              <section className="jhero" aria-label={t('journal.title')}>
                <div className="jhero-text">
                  <h2 className="jhello">{t(greetingKey)}</h2>
                  <div className="jhero-actions">
                    <button
                      type="button"
                      className="jcta"
                      onClick={() => openWriter()}
                      aria-label={t('journal.cta.write')}
                    >
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth={1.7}
                      >
                        <path d="M5 4h9a2 2 0 0 1 2 2v14a1 1 0 0 1-1 1H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1z" />
                        <path d="M5 4v15" />
                        <path d="M15 7l4-2-1.5 5.5L15 13z" />
                      </svg>
                      {t('journal.cta.write')}
                    </button>
                  </div>
                  {weekDays > 0 && (
                    <span className="jweek">{t('journal.week.days', { n: weekDays })}</span>
                  )}
                </div>
                <div className="jorb-stack">
                  <div
                    className="jorb"
                    style={{ ['--jorb' as string]: moodColor(todayEntry?.mood) } as CSSProperties}
                    role="img"
                    aria-label={todayEntry?.mood ?? t('journal.feeling.ask')}
                  />
                  <span className="jorb-label">{todayEntry?.mood ?? t('journal.feeling.ask')}</span>
                </div>
              </section>

              <hr className="jhair" />

              {/* Calendar as a warm month ribbon — derived from loaded entries. */}
              <section className="jribbon-section" aria-label={t('journal.ribbon.title')}>
                <div className="jribbon-head">
                  <h3 className="jribbon-month">{monthName}</h3>
                  <span className="jribbon-count">
                    {monthEntryDays > 0
                      ? t('journal.ribbon.count.days', { n: monthEntryDays })
                      : t('journal.ribbon.count.zero')}
                  </span>
                </div>
                <div ref={ribbonRef} className="jribbon">
                  {monthDays.map((d) => {
                    const enabled = !d.future;
                    return (
                      <button
                        type="button"
                        key={d.key}
                        data-daykey={d.key}
                        className={`jday${d.hasEntry ? ' has' : ''}${d.isToday ? ' today' : ''}`}
                        style={{ ['--dot' as string]: moodColor(d.mood) } as CSSProperties}
                        tabIndex={ribbonFocusKey === d.key ? 0 : -1}
                        disabled={!enabled}
                        onClick={() => onRibbonDay(d)}
                        onKeyDown={onRibbonKey}
                        title={`${d.date.toLocaleDateString(undefined, {
                          weekday: 'short',
                          day: 'numeric',
                          month: 'short',
                        })}${d.mood ? ` · ${d.mood}` : ''}`}
                        aria-label={d.date.toLocaleDateString(undefined, {
                          weekday: 'long',
                          day: 'numeric',
                          month: 'long',
                        })}
                      >
                        <span className="jday-dow">
                          {d.date.toLocaleDateString(undefined, { weekday: 'narrow' })}
                        </span>
                        <span className="jday-dot" />
                        <span className="jday-num">{d.date.getDate()}</span>
                      </button>
                    );
                  })}
                </div>
              </section>

              <hr className="jhair" />

              {/* Soft prompt chips — a gentle way in. */}
              <section aria-label={t('journal.prompts.eyebrow')}>
                <p className="jeyebrow jprompts-eyebrow">{t('journal.prompts.eyebrow')}</p>
                <div className="jprompts">
                  {PROMPTS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      className="jprompt"
                      onClick={() => openWriter({ prompt: t(p) })}
                    >
                      {t(p)}
                    </button>
                  ))}
                </div>
              </section>

              <hr className="jhair" />

              {/* Stats line + section head — OD ``.stats-line`` / ``.section-head``.
                Counts are honest (derived from loaded entries); month count is
                days-with-entries this month, not a fabricated total. */}
              <div className="stats-line tnum">
                <span>
                  <span className="n">{entries.length}</span>{' '}
                  {t('journal.stats.entries', { n: '' }).trim()}
                </span>
                <span className="sep">·</span>
                <span>
                  {t('journal.stats.month', { n: '' }).trim()}{' '}
                  <span className="n">{monthEntryDays}</span>
                </span>
              </div>
              <div className="section-head">
                <h2>{t('journal.section.entries')}</h2>
                <span className="hint">{t('journal.section.hint')}</span>
              </div>

              {/* Recent timeline — day groups + entries, or a state surface. */}
              <section aria-label={t('journal.recent.title')}>
                {!loaded ? (
                  <div aria-busy="true" aria-label={t('journal.loading')}>
                    <div className="jskel-row">
                      <div className="jskel" style={{ width: 120, height: 12 }} />
                      <div className="jskel" style={{ width: 'min(360px,80%)', height: 40 }} />
                      <div className="jskel" style={{ width: 'min(300px,65%)', height: 16 }} />
                    </div>
                    <div className="jskel-row">
                      <div className="jskel" style={{ width: 110, height: 12 }} />
                      <div className="jskel" style={{ width: 'min(220px,55%)', height: 20 }} />
                      <div
                        className="jskel"
                        style={{ width: '100%', maxWidth: '62ch', height: 14 }}
                      />
                      <div
                        className="jskel"
                        style={{ width: '92%', maxWidth: '58ch', height: 14 }}
                      />
                      <div
                        className="jskel"
                        style={{ width: '60%', maxWidth: '36ch', height: 14 }}
                      />
                    </div>
                    <div className="jskel-row">
                      <div className="jskel" style={{ width: 'min(180px,45%)', height: 20 }} />
                      <div
                        className="jskel"
                        style={{ width: '100%', maxWidth: '62ch', height: 14 }}
                      />
                      <div
                        className="jskel"
                        style={{ width: '75%', maxWidth: '46ch', height: 14 }}
                      />
                    </div>
                  </div>
                ) : errored ? (
                  <output className="jerror">
                    <div>
                      <p className="jerror-msg">{t('journal.error.msg')}</p>
                      <p className="jerror-sub">{t('journal.error.sub')}</p>
                    </div>
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost jerror-retry"
                      onClick={() => refresh()}
                    >
                      {t('journal.error.retry')}
                    </button>
                  </output>
                ) : entries.length === 0 && !hasFilters ? (
                  /* Empty diary — a soft illustration, one gentle sentence, the CTA. */
                  <div className="jempty">
                    <svg className="jempty-ill" aria-hidden="true" viewBox="0 0 120 132">
                      <path
                        d="M60 118 C 60 84, 60 56, 60 28"
                        strokeWidth={2}
                        strokeLinecap="round"
                        style={{
                          fill: 'none',
                          stroke: 'color-mix(in srgb, var(--diary-soft), transparent 40%)',
                        }}
                      />
                      <path
                        d="M60 62 C 44 58, 34 46, 33 30 C 49 32, 59 44, 60 62 Z"
                        style={{
                          fill: 'color-mix(in srgb, var(--mood-grateful), transparent 55%)',
                        }}
                      />
                      <path
                        d="M60 46 C 76 42, 86 30, 87 14 C 71 16, 61 28, 60 46 Z"
                        style={{
                          fill: 'color-mix(in srgb, var(--mood-grateful), transparent 40%)',
                        }}
                      />
                      <path
                        d="M60 86 C 48 83, 41 75, 40 64 C 51 66, 59 74, 60 86 Z"
                        style={{ fill: 'color-mix(in srgb, var(--purple), transparent 68%)' }}
                      />
                      <ellipse
                        cx="60"
                        cy="122"
                        rx="26"
                        ry="4"
                        style={{ fill: 'color-mix(in srgb, var(--diary-soft), transparent 82%)' }}
                      />
                    </svg>
                    <h3>{t('journal.empty.title')}</h3>
                    <p className="jempty-text">{t('journal.empty.body')}</p>
                    <button type="button" className="jcta" onClick={() => openWriter()}>
                      {t('journal.empty.cta')}
                    </button>
                  </div>
                ) : entries.length === 0 && hasFilters ? (
                  <p className="jtimeline-empty">{t('journal.timeline.empty.filtered')}</p>
                ) : (
                  <div className="guest-sample guest-journal">
                    {entries.map((e) => {
                      const fromChat = Boolean(e.source_convo_id);
                      const lvl = SALIENCE_TO_LEVEL(e.salience);
                      const matterValue = Math.round(e.salience * 10);
                      return (
                        <article className="gentry" key={e.id} data-daykey={dayKey(e.created_at)}>
                          <div className="gentry__body serif">
                            {e.title && <div className="jtitle">{e.title}</div>}
                            <EntryBody
                              body={e.body}
                              expanded={expanded.has(e.id)}
                              onToggle={() => toggleExpand(e.id)}
                              t={t}
                            />
                          </div>
                          <div className="gentry__foot">
                            <span className="gentry__date">
                              {entryDateLabel(e.created_at, dateLocale)}
                            </span>
                            {e.mood && (
                              <span className="chip">
                                <span
                                  className="gdotm"
                                  style={{ background: moodColor(e.mood) }}
                                  aria-hidden="true"
                                />
                                {e.mood}
                              </span>
                            )}
                            {e.tags.length > 0 && (
                              <div className="gentry__tags">
                                {e.tags.map((tg) => (
                                  <span key={tg} className="chip chip--dim">
                                    {tg}
                                  </span>
                                ))}
                              </div>
                            )}
                            {fromChat && (
                              <span className="chip chip--dim">{t('journal.fromchat.bare')}</span>
                            )}
                            {lvl > 0 && (
                              <span className="gentry__matter">
                                {t('journal.matters')}
                                <span className="bar">
                                  <i style={{ width: `${matterValue * 10}%` }} />
                                </span>
                                <span className="tnum">{matterValue}</span>
                              </span>
                            )}
                          </div>
                          <div className="gentry__actions">
                            <div className="jactions">
                              <button type="button" onClick={() => beginEdit(e)}>
                                {t('journal.edit')}
                              </button>
                              {confirmingDelete === e.id ? (
                                <span className="jconfirmdel">
                                  <span className="jconf-q">{t('journal.delete.confirm.q')}</span>
                                  <button
                                    type="button"
                                    className="jyes"
                                    onClick={() => doDelete(e.id)}
                                  >
                                    {t('journal.delete.confirm.yes')}
                                  </button>
                                  <button
                                    type="button"
                                    className="jno"
                                    onClick={() => setConfirmingDelete(null)}
                                  >
                                    {t('journal.delete.confirm.no')}
                                  </button>
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  className="jdel"
                                  onClick={() => setConfirmingDelete(e.id)}
                                >
                                  {t('journal.delete')}
                                </button>
                              )}
                            </div>
                          </div>
                        </article>
                      );
                    })}
                    <p className="guest-author-note">{t('journal.author.note')}</p>
                  </div>
                )}
              </section>

              <hr className="jhair" />

              {/* A quiet daily quote. */}
              <div className="jfoot">
                <figure className="jquote">
                  <p className="jquote-text">{t(quoteKey)}</p>
                </figure>
              </div>
            </div>
          ) : (
            <div className="jwrite" key="write">
              <div className="jwrite-wrap">
                <button
                  type="button"
                  className="jback"
                  onClick={() => {
                    resetComposer();
                    setMode('home');
                  }}
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.8}
                  >
                    <path d="M15 6l-6 6 6 6" />
                  </svg>
                  {t('journal.write.back')}
                </button>

                <div className="jsheet">
                  <div className="jsheet-head">
                    <span className="jwrite-eyebrow">{t('journal.write.eyebrow')}</span>
                    <span className="jwrite-date">{writeDate}</span>
                  </div>
                  {promptHint && <p className="jwrite-prompt">{promptHint}</p>}

                  {/* The page — a borderless serif sheet; no "form" feel. */}
                  <div className="jpage">
                    <input
                      className="jwrite-title"
                      value={draft.title}
                      onChange={(e) => {
                        setDraft((d) => ({ ...d, title: e.target.value }));
                        queueDraft();
                      }}
                      placeholder={t('journal.title.ph')}
                      aria-label={t('journal.title.ph')}
                    />
                    <textarea
                      ref={bodyRef}
                      className="jwrite-body"
                      id="jcomposer-body"
                      value={draft.body}
                      onChange={(e) => {
                        setDraft((d) => ({ ...d, body: e.target.value }));
                        autoGrow();
                        queueDraft();
                      }}
                      placeholder={t('journal.write.prompt.placeholder')}
                      aria-label={t('journal.write.prompt.placeholder')}
                    />
                  </div>

                  <div className="jcontrols">
                    {/* Mood quick-picker + a custom-word field. */}
                    <fieldset className="jmood-field">
                      <legend className="jctl-label">
                        {t('journal.mood.label')}{' '}
                        <span className="asst">{t('journal.mood.asst')}</span>
                      </legend>
                      <div className="jmoodpicker">
                        {MOODS.map((m) => {
                          const label = t(m.lk);
                          const on = moodDraftLc === label.toLowerCase();
                          return (
                            <button
                              key={m.lk}
                              type="button"
                              className="jmood-chip"
                              aria-pressed={on}
                              style={{ ['--mc' as string]: m.color } as CSSProperties}
                              onClick={() => {
                                setDraft((d) => ({ ...d, mood: on ? '' : label }));
                                queueDraft();
                              }}
                              title={label}
                            >
                              <span className="jmood-emoji">{m.emoji}</span>
                              <span className="jmood-word">{label}</span>
                            </button>
                          );
                        })}
                        <input
                          className="jmood-custom"
                          value={draft.mood}
                          onChange={(e) => {
                            setDraft((d) => ({ ...d, mood: e.target.value }));
                            queueDraft();
                          }}
                          placeholder={t('journal.mood.ph')}
                          aria-label={t('journal.mood')}
                          list="jcomposer-moods"
                        />
                        <datalist id="jcomposer-moods">
                          {observedMoods.map((m) => (
                            <option key={m} value={m} />
                          ))}
                        </datalist>
                      </div>
                    </fieldset>

                    {/* "Matters to me" — 1..3 stars → salience. */}
                    <fieldset className="jmatters">
                      <legend className="jctl-label">{t('journal.matters')}</legend>
                      <div className="seg">
                        {[1, 2, 3].map((lvl) => (
                          <button
                            key={lvl}
                            type="button"
                            className="jstar-btn"
                            aria-pressed={draft.matters >= lvl && draft.matters > 0}
                            onClick={() =>
                              setDraft((d) => ({
                                ...d,
                                matters: d.matters === lvl ? 0 : (lvl as 1 | 2 | 3),
                              }))
                            }
                            title={t('journal.matters.lvl', { n: lvl })}
                          >
                            {'★'.repeat(lvl)}
                          </button>
                        ))}
                      </div>
                      <p className="jmatters-note">{t('journal.matters.note')}</p>
                    </fieldset>

                    {/* Tags — type a word, press Enter to add; click a chip to remove. */}
                    <fieldset className="jtags-field">
                      <legend className="jctl-label">{t('journal.tags')}</legend>
                      <div className="jtags">
                        <input
                          className="jtag-input"
                          value={tagInput}
                          onChange={(e) => setTagInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              addTag();
                            }
                          }}
                          placeholder={t('journal.tags.ph')}
                          aria-label={t('journal.tags')}
                        />
                        {draft.tags.map((tg) => (
                          <button
                            key={tg}
                            type="button"
                            className="jtag-live"
                            aria-label={t('journal.tag.remove.aria', { t: tg })}
                            onClick={() => {
                              setDraft((d) => ({ ...d, tags: d.tags.filter((x) => x !== tg) }));
                              queueDraft();
                            }}
                          >
                            {tg}{' '}
                            <span className="x" aria-hidden="true">
                              ×
                            </span>
                          </button>
                        ))}
                      </div>
                    </fieldset>

                    {journalSeed && (
                      <div className="jfromchat-note">{t('journal.fromchat.bare')}</div>
                    )}

                    <div className="jwrite-foot">
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={save}
                        disabled={!canSave}
                      >
                        {t('journal.save')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        onClick={() => {
                          resetComposer();
                          setMode('home');
                        }}
                      >
                        {t('journal.cancel')}
                      </button>
                      <output
                        className={`jdraft-hint${draftHint ? ' show' : ''}`}
                        aria-live="polite"
                      >
                        {draftHint ?? ''}
                      </output>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
