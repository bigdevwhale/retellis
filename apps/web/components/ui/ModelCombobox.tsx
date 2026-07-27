'use client';

// Searchable model picker — the Open-Design-style combobox the user asked for.
//
// An <input role="combobox"> filters a curated model list (per-kind, in
// lib/fixtures.tsx) by prefix / substring / vendor-prefix; a "Use '{typed}'
// (custom)" row sits at the top whenever the typed value isn't an exact
// match in the curated list, so any model id (newly released, OpenRouter
// route, etc.) can be entered freely. Enter commits the active row; Escape
// closes without committing; click-outside closes.
//
// The component is self-contained — no store dep, no fetch. The parent owns
// the onCommit callback and decides whether the new value persists (e.g. the
// OnboardingScreen summary card calls PATCH /v1/providers/{id}).

import { filterModels } from '@/lib/filterModels';
import { useLang } from '@/lib/i18n';
import { type ProviderKind, suggestedModels } from '@/lib/providerCatalog';
import { useCallback, useEffect, useRef, useState } from 'react';

export const CUSTOM_SENTINEL = '__custom__';

type Props = {
  kind: ProviderKind;
  value: string; // initial / controlled current value
  onCommit: (v: string) => void;
  disabled?: boolean;
};

// Re-exported here for back-compat with any test that imported it from the
// component module. The canonical home is @/lib/filterModels — kept in a
// separate file so the unit test can hit it without dragging the @/ alias
// chain into vitest's resolver.
export { filterModels };

function Highlight({ text, start, len }: { text: string; start: number; len: number }) {
  // Render the matched substring in the purple "hl" class so the user can
  // see *why* a row matched. Safe even when start is -1 — the parent only
  // passes a real start when there is a hit.
  if (start < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, start)}
      <span className="hl">{text.slice(start, start + len)}</span>
      {text.slice(start + len)}
    </>
  );
}

export function ModelCombobox({ kind, value, onCommit, disabled }: Props) {
  const { t } = useLang();
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  // The dropdown is rendered ``position: fixed`` anchored to the input's
  // viewport rect (via the --cbx-top/left/width CSS vars consumed in
  // globals.css). Fixed positioning is what lets the menu escape an
  // overflow-clipped ancestor — specifically the add-key modal
  // (``.picker { overflow-y: auto; max-height }``) that would otherwise
  // clip an absolutely-positioned menu. The onboarding inline card has no
  // scroll-container ancestor, so absolute worked there; fixed works in
  // both. See globals.css `@media (min-width: 621px) .cbx-menu`.
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);

  const matches = filterModels([...suggestedModels(kind)], query);
  const typed = query.trim();
  // The custom row only appears when the user has typed something AND it
  // isn't already an exact match in the curated list. Otherwise the curated
  // row IS the typed value and we don't need a separate "use as custom" row.
  const showCustom = typed.length > 0 && !suggestedModels(kind).includes(typed);
  const items = showCustom ? [`${CUSTOM_SENTINEL}::${typed}`, ...matches] : matches;

  // Re-clamp the active cursor when the filtered list changes (e.g. the user
  // keeps typing and the list shrinks past the current cursor). Without this
  // the keyboard nav would point past the end of the list.
  useEffect(() => {
    if (active > Math.max(0, items.length - 1)) setActive(0);
  }, [items.length, active]);

  // Click outside → close. ``mousedown`` (not ``click``) so the down event on
  // a listbox option fires before the input's blur — without this, picking
  // a row would first close the menu and then fail to commit.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Measure the input's viewport rect and stash it as CSS vars for the
  // fixed-position menu. Re-run on open and on any scroll/resize so the
  // menu stays glued to the input when the modal or page scrolls. A
  // capturing ``scroll`` listener catches scroll events fired on any
  // ancestor (scroll doesn't bubble), so modal-internal scroll is covered.
  // If the input is near the viewport bottom (not enough room for the
  // menu below), flip the menu above the input.
  const updatePos = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const MENU_MAX = 240;
    const spaceBelow = window.innerHeight - r.bottom;
    const top = spaceBelow < MENU_MAX + 16 ? Math.max(8, r.top - MENU_MAX - 6) : r.bottom + 6;
    setPos({ top, left: r.left, width: r.width });
  }, []);

  useEffect(() => {
    if (!open) return;
    updatePos();
    window.addEventListener('scroll', updatePos, true);
    window.addEventListener('resize', updatePos);
    return () => {
      window.removeEventListener('scroll', updatePos, true);
      window.removeEventListener('resize', updatePos);
    };
  }, [open, updatePos]);

  const commit = (v: string) => {
    const final = v.trim();
    if (!final) return;
    setQuery(final);
    setOpen(false);
    onCommit(final);
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(Math.max(0, items.length - 1), a + 1));
      setOpen(true);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const it = items[active];
      if (it) {
        // The custom row is encoded as ``__custom__::<typed>``; strip the
        // sentinel so the committed value is just the typed id.
        const isCustom = it.startsWith(`${CUSTOM_SENTINEL}::`);
        commit(isCustom ? it.slice(`${CUSTOM_SENTINEL}::`.length) : it);
      } else if (typed) {
        // Empty list with typed text — treat the typed value as a custom id.
        commit(typed);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
    // Tab: default blur; don't preventDefault so focus moves naturally.
  };

  return (
    <div
      className="cbx"
      ref={rootRef}
      style={
        pos
          ? ({
              '--cbx-top': `${pos.top}px`,
              '--cbx-left': `${pos.left}px`,
              '--cbx-width': `${pos.width}px`,
            } as React.CSSProperties)
          : undefined
      }
    >
      <input
        ref={inputRef}
        className="input mono"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-controls="cbx-menu"
        aria-activedescendant={open ? `cbx-opt-${active}` : undefined}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        disabled={disabled}
        placeholder={t('onb.model.placeholder')}
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        inputMode="text"
        enterKeyHint="enter"
      />
      {open && (
        <ul id="cbx-menu" className="cbx-menu">
          {items.length === 0 && (
            <li className="cbx-opt disabled" aria-disabled="true">
              {t('onb.model.cbx.empty')}
            </li>
          )}
          {items.map((it, i) => {
            const isCustom = it.startsWith(`${CUSTOM_SENTINEL}::`);
            const label = isCustom ? it.slice(`${CUSTOM_SENTINEL}::`.length) : it;
            // Highlight the matched substring for curated rows. -1 = no hit
            // (only happens for the custom row, which is rendered differently).
            const idx = !isCustom ? label.toLowerCase().indexOf(typed.toLowerCase()) : -1;
            return (
              <li
                id={`cbx-opt-${i}`}
                key={`${i}-${label}`}
                aria-selected={i === active}
                className={`cbx-opt${i === active ? ' active' : ''}`}
                onMouseEnter={() => setActive(i)}
                // mousedown (not click) so we commit BEFORE the input blurs;
                // a click handler would race with onBlur on the input.
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(label);
                }}
              >
                {isCustom ? (
                  <span className="cbx-foot">
                    {t('onb.model.cbx.use')} "<b>{label}</b>" (custom)
                  </span>
                ) : idx >= 0 ? (
                  <Highlight text={label} start={idx} len={typed.length} />
                ) : (
                  label
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
