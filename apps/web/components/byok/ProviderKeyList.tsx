'use client';

// Renders the list of existing keys for a vault. Used by:
//   - the personal Key vault (Settings → vault tab) — each row exposes
//     "Set as active" so the user can promote a different key without
//     touching the chat-side model switcher.
//   - the family Family key tab — same rows, no "Set as active" (family
//     has no per-user active pointer).
//   - the onboarding summary card — collapsed to a single-row view of the
//     active key, with the rest behind a "Show all" toggle.
//
// Empty state is rendered by the parent — the list is only the rows.

import type { FamilyProviderRecord, ProviderRecord } from '@/lib/api-client';
import { useLang } from '@/lib/i18n';
import { type ProviderKind, isFixedOriginKind, providerMeta } from '@/lib/providerCatalog';

export type KeyRow = {
  id: string;
  kind: ProviderKind;
  label: string;
  model: string | null;
  baseUrl: string | null;
  isActive?: boolean;
};

function fromProvider(p: ProviderRecord, isActive?: boolean): KeyRow {
  return {
    id: p.id,
    kind: p.kind,
    label: p.label,
    model: p.model,
    baseUrl: p.base_url,
    isActive,
  };
}

function fromFamilyProvider(p: FamilyProviderRecord): KeyRow {
  return {
    id: p.id,
    kind: p.kind,
    label: p.label,
    model: p.model,
    baseUrl: p.base_url,
  };
}

type Props = {
  // Either personal or family rows; the component is shape-uniform.
  providers?: ProviderRecord[];
  familyProviders?: FamilyProviderRecord[];
  activeProviderId?: string | null;
  onSetActive?: (id: string) => void;
  onRemove?: (id: string) => void;
  busy?: boolean;
};

export function ProviderKeyList({
  providers,
  familyProviders,
  activeProviderId,
  onSetActive,
  onRemove,
  busy,
}: Props) {
  const { t } = useLang();
  const rows: KeyRow[] = providers
    ? providers.map((p) => fromProvider(p, p.id === activeProviderId))
    : (familyProviders ?? []).map(fromFamilyProvider);

  if (rows.length === 0) return null;

  return (
    <ul className="byok-list">
      {rows.map((row) => {
        const meta = providerMeta(row.kind);
        const showEndpoint =
          row.baseUrl && !isFixedOriginKind(row.kind) && row.baseUrl !== meta.defaultBaseUrl;
        return (
          <li key={row.id} className="byok-row">
            <div className="byok-row-main">
              <span className="byok-kind-chip" data-kind={row.kind}>
                {meta.label}
              </span>
              <span className="byok-row-label">
                {row.label}
                {row.isActive && <span className="byok-active-pill">{t('set.vault.active')}</span>}
              </span>
              {row.model && <span className="byok-row-model mono">{row.model}</span>}
              {showEndpoint && (
                <span className="byok-row-endpoint mono" title={row.baseUrl ?? undefined}>
                  {row.baseUrl}
                </span>
              )}
            </div>
            {(onSetActive || onRemove) && (
              <div className="byok-row-actions">
                {onSetActive && !row.isActive && (
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => onSetActive(row.id)}
                    disabled={busy}
                  >
                    {t('set.vault.set_active')}
                  </button>
                )}
                {onRemove && (
                  <button
                    type="button"
                    className="btn small ghost danger"
                    onClick={() => onRemove(row.id)}
                    disabled={busy}
                  >
                    ×
                  </button>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
