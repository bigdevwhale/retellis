'use client';

import type { Persona } from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';

// Redesigned via Open Design (stillside-persona-memory-redesign): a clean card
// with a quiet hover-revealed action row (Select / Chat / Edit / Delete) and an
// "Active" chip on the selected card. The opening-line quote and the glow blob
// are dropped — the preview card already shows the opening line in create mode,
// and the card reads calmer without them. Presentation only; all store logic is
// unchanged. Each action stopsPropagation so clicking it doesn't also select.
export function PersonaCard({
  persona,
  selected,
  index,
  onSelect,
  onChat,
  onEdit,
  onDelete,
}: {
  persona: Persona;
  selected: boolean;
  index: number;
  onSelect: () => void;
  onChat: () => void;
  // Custom-persona affordances: shown only when persona.custom is true. Both
  // stopPropagation so clicking them doesn't also select the card.
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const { t, L2 } = useLang();
  const stop = (fn: () => void) => (e: { stopPropagation: () => void }) => {
    e.stopPropagation();
    fn();
  };
  return (
    <div
      className={`pc${selected ? ' sel' : ''}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      style={{ animationDelay: `${index * 60}ms` }}
    >
      {selected ? <span className="chip pc-active">{t('ps.active')}</span> : null}
      <div className="av" style={{ background: persona.grad }}>
        {persona.glyph}
      </div>
      <div className="pn">{persona.name}</div>
      <div className="pr">{L2(persona.role)}</div>
      <div className="pv">{L2(persona.vibe)}</div>
      <div className="pc-actions">
        <button type="button" className="btn btn-sm btn-quiet pc-act" onClick={stop(onSelect)}>
          {t('ps.select')}
        </button>
        <button type="button" className="btn btn-sm btn-quiet pc-act" onClick={stop(onChat)}>
          {t('ps.chat')}
        </button>
        {persona.custom && onEdit ? (
          <button type="button" className="btn btn-sm btn-quiet pc-act" onClick={stop(onEdit)}>
            {t('ps.edit')}
          </button>
        ) : null}
        {persona.custom && onDelete ? (
          <button
            type="button"
            className="btn btn-sm btn-danger-ghost pc-act"
            onClick={stop(onDelete)}
          >
            {t('ps.delete')}
          </button>
        ) : null}
      </div>
    </div>
  );
}
