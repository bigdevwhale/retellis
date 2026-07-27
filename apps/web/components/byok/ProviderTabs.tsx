'use client';

// The 8-provider tabs at the top of the BYOK form. Mirrors Open Design's
// `API_PROTOCOL_TABS` strip — a `<button role="tab">` per kind, an
// aria-selected indicator on the active one, and keyboard nav via
// ArrowLeft/ArrowRight (the standard radiogroup-on-tabs pattern).

import { useLang } from '@/lib/i18n';
import { PROVIDER_ORDER, type ProviderKind, providerMeta } from '@/lib/providerCatalog';
import { useEffect, useRef } from 'react';

type Props = {
  value: ProviderKind;
  onChange: (kind: ProviderKind) => void;
  disabled?: boolean;
};

export function ProviderTabs({ value, onChange, disabled }: Props) {
  const { t } = useLang();
  const rootRef = useRef<HTMLDivElement>(null);

  // Focus management: when the user arrow-keys through the tabs, the active
  // tab gets the focus (W3C tab-pattern). Without this, the previously
  // focused tab keeps the focus ring and the active one is purely visual.
  useEffect(() => {
    const el = rootRef.current?.querySelector<HTMLButtonElement>(`[data-kind="${value}"]`);
    if (el && document.activeElement?.closest('.byok-tabs')) el.focus();
  }, [value]);

  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const idx = PROVIDER_ORDER.indexOf(value);
    if (idx < 0) return;
    const step = e.key === 'ArrowRight' ? 1 : -1;
    const next = PROVIDER_ORDER[(idx + step + PROVIDER_ORDER.length) % PROVIDER_ORDER.length];
    if (next) onChange(next);
  };

  return (
    <div
      ref={rootRef}
      role="tablist"
      aria-label={t('set.vault')}
      className="byok-tabs"
      onKeyDown={onKey}
    >
      {PROVIDER_ORDER.map((kind) => {
        const meta = providerMeta(kind);
        const selected = value === kind;
        return (
          <button
            key={kind}
            type="button"
            role="tab"
            aria-selected={selected}
            data-kind={kind}
            className={`byok-tab${selected ? ' active' : ''}`}
            onClick={() => onChange(kind)}
            disabled={disabled}
            title={meta.desc.en}
          >
            {meta.label}
          </button>
        );
      })}
    </div>
  );
}
