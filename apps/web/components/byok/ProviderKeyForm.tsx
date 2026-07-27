'use client';

// The per-provider key form, shared by the personal Key vault, the family
// Family key tab, and the onboarding summary card. Mirrors Open Design's
// ByokKeyField pattern: reveal/show toggle on the key input, "Get API key"
// link to the provider console, per-protocol placeholder, fixed-origin
// gateways hide the Endpoint field, and a credential-shape branch for AWS
// Bedrock (3 fields, not 1).
//
// Submission is the parent's job: the form returns plain values and the
// parent encrypts + POSTs. Keeping the encryption flow in the parent means
// the personal vs family flows (different master keys, different API
// endpoints) don't have to be re-encoded per kind.

import { ModelCombobox } from '@/components/ui/ModelCombobox';
import { useLang } from '@/lib/i18n';
import {
  type ProviderKind,
  hasEmbeddings,
  isFixedOriginKind,
  providerMeta,
  resolveEffectiveBaseUrl,
  suggestedModels,
} from '@/lib/providerCatalog';
import { useEffect, useState } from 'react';
import { ProviderTabs } from './ProviderTabs';

// What the parent receives on submit. The plain `apiKey` is the only secret —
// `extra` carries the AWS triplet for Bedrock, null for everyone else.
export type ProviderKeyFormValues = {
  kind: ProviderKind;
  label: string;
  apiKey: string;
  baseUrl: string | null;
  model: string;
  embeddingsModel: string | null;
  // AWS access key id / secret / region, populated only when the user picked
  // Bedrock. null otherwise. Sent alongside `apiKey` (which carries the access
  // key id) so the vault row can be reassembled at submit time without the
  // user re-typing. The "access key id" also lives in `apiKey` for symmetry
  // with the rest of the chain (every other kind has a single Bearer key).
  extra: Record<string, string> | null;
};

type Props = {
  initial?: Partial<ProviderKeyFormValues> & { kind?: ProviderKind };
  onSubmit: (values: ProviderKeyFormValues) => void | Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
  busy?: boolean;
};

// Per-kind validation rules. Centralized so a future kind is one entry. The
// "single" shape (default) requires only `apiKey` (length ≥ 8 — same bar as
// the onboarding form). The "aws" shape (Bedrock) requires the three
// credential fields plus the access key id in `apiKey` for symmetry.
const MIN_KEY_LEN = 8;
const MIN_AWS_SECRET_LEN = 16;

export function ProviderKeyForm({ initial, onSubmit, onCancel, submitLabel, busy }: Props) {
  const { t, L2 } = useLang();
  const [kind, setKind] = useState<ProviderKind>(initial?.kind ?? 'openai');
  const meta = providerMeta(kind);
  const [apiKey, setApiKey] = useState(initial?.apiKey ?? '');
  const [showKey, setShowKey] = useState(false);
  const [label, setLabel] = useState(initial?.label ?? `${meta.label} key`);
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl ?? meta.defaultBaseUrl);
  const [model, setModel] = useState(initial?.model ?? meta.defaultModel);
  const [embeddingsModel, setEmbeddingsModel] = useState(
    initial?.embeddingsModel ?? meta.embeddingsDefault ?? '',
  );
  // AWS-specific. Only meaningful for Bedrock; for other kinds the inputs are
  // not rendered and the state stays untouched.
  const [awsSecret, setAwsSecret] = useState('');
  const [awsRegion, setAwsRegion] = useState('');

  // When the user switches tabs, the per-kind defaults that make sense (label,
  // base URL, model) should follow — the user's typed key is the only thing
  // that persists across the switch, so they don't lose work by glancing at a
  // different provider and switching back.
  useEffect(() => {
    const m = providerMeta(kind);
    setLabel((prev) => (prev && !prev.endsWith(' key') ? prev : `${m.label} key`));
    // Fixed-origin gateways overwrite any baseUrl the user typed — the field
    // isn't shown for those kinds, so an old value would otherwise leak.
    if (isFixedOriginKind(kind)) {
      setBaseUrl(m.defaultBaseUrl);
    } else if (!initial?.baseUrl) {
      setBaseUrl(m.defaultBaseUrl);
    }
    setModel((prev) => (prev && !suggestedModels(kind).includes(prev) ? prev : m.defaultModel));
    if (hasEmbeddings(kind)) {
      setEmbeddingsModel((prev) => prev || m.embeddingsDefault || '');
    } else {
      setEmbeddingsModel('');
    }
  }, [kind, initial?.baseUrl]);

  const isAws = meta.credentialShape === 'aws';
  const showEndpoint = !isFixedOriginKind(kind) && !isAws;
  const showEmbeddings = hasEmbeddings(kind);

  const apiKeyOk = apiKey.trim().length >= MIN_KEY_LEN;
  const labelOk = label.trim().length > 0;
  const awsOk =
    !isAws || (awsSecret.trim().length >= MIN_AWS_SECRET_LEN && awsRegion.trim().length > 0);
  const canSubmit = apiKeyOk && labelOk && awsOk && !busy;

  const handle = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    const values: ProviderKeyFormValues = {
      kind,
      label: label.trim(),
      apiKey: apiKey.trim(),
      baseUrl: isFixedOriginKind(kind)
        ? resolveEffectiveBaseUrl(kind, baseUrl)
        : baseUrl.trim() || null,
      model: model.trim(),
      embeddingsModel: showEmbeddings ? embeddingsModel.trim() || null : null,
      extra: isAws
        ? {
            // The Bedrock access key id is the same value as `apiKey` so the
            // server-side adapter can read either path without having to know
            // which is canonical. The LiteLLM adapter pulls `aws_access_key_id`
            // from `extra`, but we mirror it in `apiKey` for surface uniformity
            // (e.g. logging shows the same id, masked).
            aws_access_key_id: apiKey.trim(),
            aws_secret_access_key: awsSecret.trim(),
            aws_region_name: awsRegion.trim(),
          }
        : null,
    };
    await onSubmit(values);
  };

  return (
    <form className="byok-form" onSubmit={handle}>
      <ProviderTabs value={kind} onChange={setKind} disabled={busy} />

      <p className="byok-hint" aria-hidden={false}>
        {L2(meta.desc)}
      </p>

      <label className="field">
        <span className="field-label">{t('onb.label')}</span>
        <input
          className="input"
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          disabled={busy}
          maxLength={64}
          aria-describedby="byok-label-help"
        />
        <span className="field-help" id="byok-label-help">
          {t('byok.label.help')}
        </span>
      </label>

      <label className="field">
        <span className="field-label">{isAws ? t('byok.aws.access_key') : t('onb.key')}</span>
        <div className="byok-key-row">
          <input
            className="input mono"
            type={showKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            disabled={busy}
            placeholder={meta.apiKeyPlaceholder}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            aria-invalid={apiKey.length > 0 && !apiKeyOk}
          />
          <button
            type="button"
            className="byok-eye"
            onClick={() => setShowKey((s) => !s)}
            disabled={busy}
            aria-label={showKey ? t('byok.hide_key') : t('byok.show_key')}
            title={showKey ? t('byok.hide_key') : t('byok.show_key')}
          >
            {showKey ? t('byok.hide_key') : t('byok.show_key')}
          </button>
          <a
            className="byok-console"
            href={meta.apiKeyConsoleUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t('byok.get_api_key')}
          </a>
        </div>
      </label>

      {isAws && (
        <>
          <label className="field">
            <span className="field-label">{t('byok.aws.secret_key')}</span>
            <input
              className="input mono"
              type="password"
              value={awsSecret}
              onChange={(e) => setAwsSecret(e.target.value)}
              disabled={busy}
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              aria-invalid={awsSecret.length > 0 && awsSecret.length < MIN_AWS_SECRET_LEN}
            />
          </label>
          <label className="field">
            <span className="field-label">{t('byok.aws.region')}</span>
            <input
              className="input"
              type="text"
              value={awsRegion}
              onChange={(e) => setAwsRegion(e.target.value)}
              disabled={busy}
              placeholder="us-east-1"
              autoComplete="off"
              spellCheck={false}
              aria-invalid={awsRegion.length > 0 && !awsRegion.trim()}
            />
            <span className="field-help">{t('byok.aws.help')}</span>
          </label>
        </>
      )}

      {showEndpoint && (
        <label className="field">
          <span className="field-label">{t('byok.endpoint')}</span>
          <input
            className="input"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            disabled={busy}
            placeholder={meta.defaultBaseUrl || 'https://...'}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
          <span className="field-help">{t('byok.endpoint.help')}</span>
        </label>
      )}

      <div className="field">
        <span className="field-label">{t('onb.model')}</span>
        <ModelCombobox kind={kind} value={model} onCommit={(v) => setModel(v)} disabled={busy} />
      </div>

      {showEmbeddings && (
        <label className="field">
          <span className="field-label">{t('onb.embed')}</span>
          <input
            className="input mono"
            type="text"
            value={embeddingsModel}
            onChange={(e) => setEmbeddingsModel(e.target.value)}
            disabled={busy}
            placeholder={meta.embeddingsDefault || ''}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
      )}

      {onCancel && (
        <div className="byok-actions">
          <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>
            {t('byok.cancel')}
          </button>
          <button type="submit" className="btn primary" disabled={!canSubmit}>
            {busy ? t('byok.saving') : (submitLabel ?? t('byok.add'))}
          </button>
        </div>
      )}
      {!onCancel && (
        <button type="submit" className="btn primary byok-submit" disabled={!canSubmit}>
          {busy ? t('byok.saving') : (submitLabel ?? t('byok.add'))}
        </button>
      )}
    </form>
  );
}
