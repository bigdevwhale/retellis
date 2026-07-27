'use client';

import {
  AVATAR_PALETTE,
  PERSONAS,
  PROMPT_PRESETS,
  type Persona,
  avatarPaletteByGrad,
  personaById,
} from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';
import { useStore } from '@/lib/store';
import { useRouter, useSearchParams } from 'next/navigation';
import { type CSSProperties, useEffect, useState } from 'react';
import { PersonaCard } from './PersonaCard';

export function PersonaScreen() {
  const { t, L2 } = useLang();
  const router = useRouter();
  const search = useSearchParams();
  const tab = search.get('tab') === 'create' ? 'create' : 'gallery';
  const editId = search.get('edit');

  const personas = useStore((s) => s.personas);
  const list = personas();
  const activePersona = useStore((s) => s.activePersonaId);
  const setActivePersona = useStore((s) => s.setActivePersona);
  const startChatWith = useStore((s) => s.startChatWith);
  const addPersona = useStore((s) => s.addPersona);
  const updatePersona = useStore((s) => s.updatePersona);
  const deletePersona = useStore((s) => s.deletePersona);

  const [name, setName] = useState('Sage');
  const [role, setRole] = useState('A gentle sounding board');
  const [warmth, setWarmth] = useState(82);
  const [direct, setDirect] = useState(30);
  const [pace, setPace] = useState(38);
  // The persona's voice is composed from three structured prompts instead of
  // one monolithic system prompt: specialization (who they are), character
  // (personality/manner), and approach (how they work). composePrompt() joins
  // them into the single block sent to the backend (and stored on the persona).
  const [specialization, setSpecialization] = useState('A gentle sounding board');
  const [character, setCharacter] = useState('Calm, warm, listens closely');
  const [approach, setApproach] = useState(
    'Name the emotion before offering a frame. Keep replies short and warm.',
  );
  const [open, setOpen] = useState("Hey. I'm here, no rush. What's going on?");
  // Avatar identity: index into AVATAR_PALETTE. 0 is the historical purple→
  // magenta default, so a persona saved without picking looks like before.
  const [paletteIdx, setPaletteIdx] = useState(0);
  // Tracks which template/preset the form was last seeded from so its chip can
  // be highlighted; cleared the moment the user edits any field by hand.
  const [activeTpl, setActiveTpl] = useState<string | null>(null);
  // When set, the form is editing an existing custom persona in place instead
  // of creating a new one; Save becomes Update.
  const [editingId, setEditingId] = useState<string | null>(null);

  const pal = AVATAR_PALETTE[paletteIdx] ?? AVATAR_PALETTE[0]!;

  const composePrompt = (spec: string, chr: string, appr: string) => {
    const parts = [
      spec.trim() && `Specialization: ${spec.trim()}`,
      chr.trim() && `Character: ${chr.trim()}`,
      appr.trim() && `Approach: ${appr.trim()}`,
    ].filter(Boolean) as string[];
    // Always append the safety baseline so a custom persona can't drop the
    // "disclose, don't perform" constraint by leaving a field blank.
    return [...parts, "Never claim feelings you don't have. Disclose, don't perform."].join('\n');
  };

  const useTemplate = (id: string) => {
    const p = personaById(id, list);
    setName(p.name);
    setRole(L2(p.role));
    // Seed the three structured fields from the template's own values. All
    // builtins carry them now; fall back to deriving from role/prompt for any
    // older persona that doesn't.
    setSpecialization(L2(p.specialization ?? { en: L2(p.role), ru: L2(p.role) }));
    setCharacter(L2(p.character ?? { en: '', ru: '' }));
    setApproach(L2(p.approach ?? p.prompt));
    setOpen(L2(p.open));
    setWarmth(p.tone.warmth);
    setDirect(p.tone.direct);
    setPace(p.tone.pace);
    setPaletteIdx(avatarPaletteByGrad(p.grad));
  };

  // True "Blank": clear every field to neutral defaults instead of secretly
  // seeding Aria. Tone to the midpoint, palette to the default, opening line
  // to a plain non-committal greeting.
  const resetBlank = () => {
    setName('');
    setRole('');
    setSpecialization('');
    setCharacter('');
    setApproach('');
    setOpen(L2({ en: 'Hi. Where would you like to start?', ru: 'Привет. С чего начнём?' }));
    setWarmth(50);
    setDirect(50);
    setPace(50);
    setPaletteIdx(0);
  };

  // Seed the whole form from an existing custom persona (edit mode). Mirrors
  // useTemplate but also resolves the palette from the stored gradient.
  const seedFrom = (p: Persona) => {
    setName(p.name);
    setRole(L2(p.role));
    setSpecialization(L2(p.specialization ?? { en: '', ru: '' }));
    setCharacter(L2(p.character ?? { en: '', ru: '' }));
    setApproach(L2(p.approach ?? p.prompt));
    setOpen(L2(p.open));
    setWarmth(p.tone.warmth);
    setDirect(p.tone.direct);
    setPace(p.tone.pace);
    setPaletteIdx(avatarPaletteByGrad(p.grad));
    setActiveTpl(null);
  };

  // React to ?edit= changing (including first mount and navigating back to a
  // fresh create). If the id resolves to a custom persona, seed + enter edit
  // mode; otherwise leave the default new-persona form. We seed ONLY on a
  // change of edit-target, not on every render of `list`/`seedFrom` — re-seeding
  // on each keystroke would clobber the user's in-progress edits.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional — see above
  useEffect(() => {
    if (!editId) {
      setEditingId(null);
      return;
    }
    const target = list.find((p) => p.id === editId && p.custom);
    if (target) {
      seedFrom(target);
      setEditingId(editId);
    } else {
      setEditingId(null);
    }
  }, [editId]);

  // Apply a prompt preset: fills only the three prompt fields, leaving name,
  // tone, and opening line untouched so the user can mix a preset with their
  // own persona identity.
  const applyPreset = (presetId: string) => {
    const ps = PROMPT_PRESETS.find((x) => x.id === presetId);
    if (!ps) return;
    setSpecialization(L2(ps.specialization));
    setCharacter(L2(ps.character));
    setApproach(L2(ps.approach));
  };

  // Build the persona from the current form and either add it (create) or
  // merge it into the existing custom persona (edit). Returns the id so Test
  // can open a chat with it. The structured fields are persisted too, so a
  // later edit re-seeds them instead of the composed block.
  const persist = (): string => {
    const composed = composePrompt(specialization, character, approach);
    const glyph = (name.trim()[0] ?? '?').toUpperCase();
    const roleStr = role || 'companion';
    const nameStr = name || 'Sage';
    const fields = {
      name: nameStr,
      role: { en: roleStr, ru: roleStr },
      glyph,
      grad: pal.grad,
      glow: pal.glow,
      vibe: { en: 'Your custom companion.', ru: 'Ваш собственный компаньон.' },
      open: { en: open, ru: open },
      prompt: { en: composed, ru: composed },
      specialization: { en: specialization, ru: specialization },
      character: { en: character, ru: character },
      approach: { en: approach, ru: approach },
      tone: { warmth, direct, pace },
    };
    if (editingId) {
      updatePersona(editingId, fields);
      return editingId;
    }
    const id = `custom-${Date.now().toString(36)}`;
    const p: Persona = { id, ...fields, custom: true };
    addPersona(p);
    return id;
  };

  const save = () => {
    persist();
    router.push('/persona?tab=gallery');
  };

  // Test = persist the in-progress persona, then start a chat with it so the
  // user actually talks to the draft they just shaped (not the previously
  // active persona, which is what the old button silently did).
  const test = () => {
    const id = persist();
    startChatWith(id);
    router.push('/chat');
  };

  const count = list.length;
  const mine = Math.max(0, count - PERSONAS.length);

  // The three tone axes rendered as labeled sliders. Each carries a one-line
  // hint (ps.*.tip) shown directly under the slider — the native `title`
  // tooltip is easy to miss, so the explanation is rendered visibly here too —
  // plus semantic lo/hi endpoints so the scale reads at a glance.
  const toneAxes = [
    {
      id: 'warmth',
      label: t('ps.warmth'),
      tip: t('ps.warmth.tip'),
      lo: t('ps.warmth.lo'),
      hi: t('ps.warmth.hi'),
      value: warmth,
      set: setWarmth,
    },
    {
      id: 'direct',
      label: t('ps.direct'),
      tip: t('ps.direct.tip'),
      lo: t('ps.direct.lo'),
      hi: t('ps.direct.hi'),
      value: direct,
      set: setDirect,
    },
    {
      id: 'pace',
      label: t('ps.pace'),
      tip: t('ps.pace.tip'),
      lo: t('ps.pace.lo'),
      hi: t('ps.pace.hi'),
      value: pace,
      set: setPace,
    },
  ] as const;

  return (
    <>
      <main className={tab === 'create' ? 'wrap wrap-create ps-page' : 'wrap ps-page'}>
        <div className="pagehead">
          <div className="pagehead__row">
            <div>
              <h1>{t('ps.title')}</h1>
              <p className="lede">{t('ps.lede')}</p>
            </div>
            <span className="badge badge--mute">
              <span className="dot" aria-hidden="true" />
              {t('ps.badge.five')}
            </span>
          </div>
        </div>
        <div className="seg" role="tablist" aria-label={t('ps.title')}>
          <button
            type="button"
            aria-pressed={tab === 'gallery'}
            className={tab === 'gallery' ? 'on' : ''}
            onClick={() => router.push('/persona?tab=gallery')}
          >
            {t('nav.gallery')}
          </button>
          <button
            type="button"
            aria-pressed={tab === 'create'}
            className={tab === 'create' ? 'on' : ''}
            onClick={() => router.push('/persona?tab=create')}
          >
            {t('nav.create')}
          </button>
        </div>

        {tab === 'gallery' ? (
          <>
            <h2 className="section-title">{t('ps.section.ready')}</h2>
            <div className="persona-grid stagger">
              {list.map((p, i) => (
                <PersonaCard
                  key={p.id}
                  persona={p}
                  selected={p.id === activePersona}
                  index={i}
                  onSelect={() => setActivePersona(p.id)}
                  onChat={() => {
                    startChatWith(p.id);
                    router.push('/chat');
                  }}
                  onEdit={
                    p.custom ? () => router.push(`/persona?tab=create&edit=${p.id}`) : undefined
                  }
                  onDelete={
                    p.custom
                      ? () => {
                          if (confirm(t('ps.delete.confirm'))) deletePersona(p.id);
                        }
                      : undefined
                  }
                />
              ))}
              <div
                className="create-card"
                style={{ animationDelay: `${list.length * 60}ms` }}
                onClick={() => router.push('/persona?tab=create')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    router.push('/persona?tab=create');
                  }
                }}
              >
                <div className="plus">+</div>
                <div className="ct">{t('nav.create')}</div>
                <div className="cd">
                  {L2({
                    en: 'A companion tuned exactly how you want',
                    ru: 'Компаньон, настроенный именно так, как вы хотите',
                  })}
                </div>
              </div>
            </div>
          </>
        ) : (
          <>
            <h2 className="section-title">{t('ps.section.own')}</h2>
            <div className="grid create-grid">
              <div className="card">
                <div className="card-title">
                  {editingId ? t('ps.editing.title') : t('ps.new.title')}
                </div>
                <div className="card-desc">
                  {editingId ? t('ps.editing.desc') : t('ps.new.desc')}
                </div>

                <div className="ps-sec">
                  <span className="ps-sec-l">{t('ps.sec.identity')}</span>
                  <span className="ps-sec-r" aria-hidden />
                </div>
                <div className="field">
                  <label htmlFor="ps-blank">{t('ps.startfrom')}</label>
                  <div className="tmpl-row">
                    <button
                      type="button"
                      id="ps-blank"
                      className={`tmpl-blank${activeTpl === 'blank' ? ' on' : ''}`}
                      onClick={() => {
                        resetBlank();
                        setActiveTpl('blank');
                      }}
                    >
                      {t('ps.blank')}
                    </button>
                    {PERSONAS.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        className={`tmpl${activeTpl === p.id ? ' on' : ''}`}
                        title={L2(p.role)}
                        onClick={() => {
                          useTemplate(p.id);
                          setActiveTpl(p.id);
                        }}
                      >
                        <div className="a" style={{ background: p.grad }}>
                          {p.glyph}
                        </div>
                        <span>{p.name}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="ps-row-2">
                  <div className="field">
                    <label htmlFor="ps-name">{t('ps.name')}</label>
                    <input
                      id="ps-name"
                      className="input"
                      value={name}
                      onChange={(e) => {
                        setName(e.target.value);
                        setActiveTpl(null);
                      }}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="ps-role">{t('ps.role')}</label>
                    <input
                      id="ps-role"
                      className="input"
                      value={role}
                      onChange={(e) => {
                        setRole(e.target.value);
                        setActiveTpl(null);
                      }}
                    />
                  </div>
                </div>
                <div className="field">
                  <div className="ps-flabel">{t('ps.palette')}</div>
                  <div className="ps-palette">
                    {AVATAR_PALETTE.map((sw, i) => (
                      <button
                        key={sw.id}
                        type="button"
                        className={`sw${paletteIdx === i ? ' on' : ''}`}
                        style={{ background: sw.grad }}
                        aria-label={sw.id}
                        aria-pressed={paletteIdx === i}
                        onClick={() => {
                          setPaletteIdx(i);
                          setActiveTpl(null);
                        }}
                      />
                    ))}
                  </div>
                  <div className="help" style={{ fontSize: 12, marginTop: 6 }}>
                    {t('ps.palette.help')}
                  </div>
                </div>

                <div className="ps-sec">
                  <span className="ps-sec-l">{t('ps.sec.voice')}</span>
                  <span className="ps-sec-r" aria-hidden />
                </div>
                <div className="ps-tone-stack">
                  {toneAxes.map((axis) => (
                    <div key={axis.id} className="tone-axis">
                      <div className="tone-row">
                        <label htmlFor={`ps-${axis.id}`} title={axis.tip}>
                          {axis.label}
                        </label>
                        <input
                          id={`ps-${axis.id}`}
                          className="range"
                          type="range"
                          min="0"
                          max="100"
                          value={axis.value}
                          style={{ ['--pct' as string]: `${axis.value}%` } as CSSProperties}
                          onChange={(e) => {
                            axis.set(Number(e.target.value));
                            setActiveTpl(null);
                          }}
                          aria-label={axis.tip}
                        />
                        <span className="v tnum">{axis.value}</span>
                      </div>
                      <div className="tone-ends" aria-hidden>
                        <span>{axis.lo}</span>
                        <span>{axis.hi}</span>
                      </div>
                      <div className="help tone-tip">{axis.tip}</div>
                    </div>
                  ))}
                </div>

                <div className="ps-sec">
                  <span className="ps-sec-l">{t('ps.sec.prompt')}</span>
                  <span className="ps-sec-r" aria-hidden />
                </div>
                <div className="field">
                  <div className="ps-flabel">{t('ps.presets')}</div>
                  <div className="pr-chips">
                    {PROMPT_PRESETS.map((ps) => (
                      <button
                        key={ps.id}
                        type="button"
                        className="chip"
                        title={L2(ps.specialization)}
                        onClick={() => applyPreset(ps.id)}
                      >
                        {L2(ps.label)}
                      </button>
                    ))}
                  </div>
                  <div className="help" style={{ fontSize: 12, marginTop: 6 }}>
                    {t('ps.presets.help')}
                  </div>
                </div>
                <div className="ps-row-2">
                  <div className="field">
                    <label htmlFor="ps-spec">{t('ps.spec')}</label>
                    <textarea
                      id="ps-spec"
                      className="input"
                      rows={2}
                      value={specialization}
                      onChange={(e) => {
                        setSpecialization(e.target.value);
                        setActiveTpl(null);
                      }}
                      placeholder={t('ps.spec.ph')}
                    />
                    <div className="help" style={{ fontSize: 12, marginTop: 2 }}>
                      {t('ps.spec.help')}
                    </div>
                  </div>
                  <div className="field">
                    <label htmlFor="ps-character">{t('ps.character')}</label>
                    <textarea
                      id="ps-character"
                      className="input"
                      rows={2}
                      value={character}
                      onChange={(e) => {
                        setCharacter(e.target.value);
                        setActiveTpl(null);
                      }}
                      placeholder={t('ps.character.ph')}
                    />
                    <div className="help" style={{ fontSize: 12, marginTop: 2 }}>
                      {t('ps.character.help')}
                    </div>
                  </div>
                </div>
                <div className="field">
                  <label htmlFor="ps-approach">{t('ps.approach')}</label>
                  <textarea
                    id="ps-approach"
                    className="input"
                    rows={3}
                    value={approach}
                    onChange={(e) => {
                      setApproach(e.target.value);
                      setActiveTpl(null);
                    }}
                    placeholder={t('ps.approach.ph')}
                  />
                  <div className="help" style={{ fontSize: 12, marginTop: 2 }}>
                    {t('ps.approach.help')}
                  </div>
                </div>

                <div className="ps-sec">
                  <span className="ps-sec-l">{t('ps.sec.opening')}</span>
                  <span className="ps-sec-r" aria-hidden />
                </div>
                <div className="field">
                  <label htmlFor="ps-open">{t('ps.open')}</label>
                  <input
                    id="ps-open"
                    className="input"
                    value={open}
                    onChange={(e) => {
                      setOpen(e.target.value);
                      setActiveTpl(null);
                    }}
                  />
                </div>

                <div className="ps-actions">
                  <button type="button" className="btn btn-primary" onClick={save}>
                    {editingId ? t('ps.update') : t('ps.save')}
                  </button>
                  <button type="button" className="btn" onClick={test}>
                    {t('ps.test')}
                  </button>
                </div>
              </div>
              <div className="card">
                <div className="card-title">{t('ps.preview')}</div>
                <div className="card-desc">
                  <span>{t('ps.preview.how')}</span>{' '}
                  <span style={{ color: 'var(--heading)' }}>{name || 'Sage'}</span>{' '}
                  <span>{t('ps.preview.opens')}</span>
                </div>
                <div className="preview-card">
                  <div className="pv-av">
                    <div
                      className="a"
                      style={{
                        background: pal.grad,
                        boxShadow: `0 0 22px ${pal.glow}55`,
                      }}
                    >
                      {(name.trim()[0] ?? '?').toUpperCase()}
                    </div>
                    <div className="pv-av-text">
                      <div className="n">{name || 'Sage'}</div>
                      {role && <div className="pv-role">{role}</div>}
                    </div>
                  </div>
                  <div className="pv-them">{open}</div>
                  <div className="pv-tone">
                    {toneAxes.map((axis) => (
                      <div key={axis.id} className="pv-bar">
                        <span className="pv-bar-l">{axis.label}</span>
                        <span className="pv-bar-t">
                          <span className="pv-bar-f" style={{ width: `${axis.value}%` }} />
                        </span>
                        <span className="pv-bar-v tnum">{axis.value}</span>
                      </div>
                    ))}
                  </div>
                  <details className="ps-compose">
                    <summary>{t('ps.compose')}</summary>
                    <pre>{composePrompt(specialization, character, approach)}</pre>
                    <div className="ps-compose-note">{t('ps.compose.note')}</div>
                  </details>
                </div>
                <div style={{ marginTop: 18, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <span className="chip">{t('ps.chip1')}</span>
                  <span className="chip neutral">{t('ps.chip2')}</span>
                </div>
                <div className="help" style={{ marginTop: 16 }}>
                  {t('ps.help')}
                </div>
              </div>
            </div>
          </>
        )}
      </main>

      <p className="limitline">
        <em>{t('ps.limitline')}</em>
      </p>
    </>
  );
}
