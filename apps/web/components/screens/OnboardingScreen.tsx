'use client';

import { AddProviderModal } from '@/components/byok/AddProviderModal';
import type { ProviderKeyFormValues } from '@/components/byok/ProviderKeyForm';
import { CUSTOM_SENTINEL, ModelCombobox } from '@/components/ui/ModelCombobox';
import {
  type ProviderRecord,
  createProvider,
  deleteProvider,
  getHealth,
  listProviders,
  updateProvider,
} from '@/lib/api-client';
import { useAuthCtx } from '@/lib/auth';
import { PERSONAS } from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';
import {
  PROVIDER_ORDER,
  type ProviderKind,
  hasEmbeddings,
  providerMeta,
  suggestedModels,
} from '@/lib/providerCatalog';
import { useStore } from '@/lib/store';
import { type KeyPayload, newKeyHandle, sealKeyToServer } from '@/lib/vault';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { PersonaCard } from './PersonaCard';

// Default embedding model per provider kind, sourced from the catalog. Kinds
// absent (anthropic, openrouter, bedrock) have no first-party embeddings API;
// the field is hidden for them (see ``hasEmbeddings``).
const _embDefault = (k: ProviderKind): string | undefined => providerMeta(k).embeddingsDefault;

// Whether a provider kind has a user-editable base URL field. Equivalent to
// the legacy ``providerDef(kind).baseUrl`` flag: false for fixed-origin
// gateways (AIHubMix) and for kinds whose credential shape doesn't carry one
// (Bedrock uses region instead).
const _hasBaseUrl = (k: ProviderKind): boolean =>
  k !== 'aihubmix' && providerMeta(k).credentialShape !== 'aws';

export function OnboardingScreen() {
  const { t, L2 } = useLang();
  const router = useRouter();
  // Billing is hosted-only; the "use a hosted plan" alt link points at /plans,
  // which self-hosted instances can't serve. Hide it there.
  const billing = !!useAuthCtx().config?.features.billing;
  const [kind, setKind] = useState<ProviderKind>('openai');
  const [keyVal, setKeyVal] = useState('');
  const [label, setLabel] = useState('Work OpenAI');
  const [modelChoice, setModelChoice] = useState<string>(providerMeta('openai').defaultModel);
  const [customModel, setCustomModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  // "Switch provider" flow — when a provider is already connected and the
  // user clicks "Switch provider" in the summary card, this opens the same
  // destructive confirm as the reset path.
  const [confirmingSwitch, setConfirmingSwitch] = useState(false);

  // Multi-key affordance: when a provider is already connected, the summary
  // card shows an "Add another key" button that opens this modal. The form
  // returns the new key; the handler seals it to the server and POSTs a new
  // ProviderRecord. The active provider pointer does NOT move — the user
  // keeps their current selection; they can promote the new key via the
  // chat-side model switcher or by reopening this screen.
  const [addOpen, setAddOpen] = useState(false);
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  // Server-side provider rows (read on mount so the summary card can show
  // the connected provider after a refresh, when activeProvider is null).
  const [remoteProviders, setRemoteProviders] = useState<ProviderRecord[]>([]);

  // When the provider kind changes, reset the model picker to that kind's
  // default curated model and clear any custom entry.
  useEffect(() => {
    setModelChoice(providerMeta(kind).defaultModel);
    setCustomModel('');
  }, [kind]);

  const effectiveModel = modelChoice === CUSTOM_SENTINEL ? customModel.trim() : modelChoice;

  const startChatWith = useStore((s) => s.startChatWith);
  const activePersona = useStore((s) => s.activePersonaId);
  const setActivePersona = useStore((s) => s.setActivePersona);
  const setActiveProvider = useStore((s) => s.setActiveProvider);
  const updateActiveProvider = useStore((s) => s.updateActiveProvider);
  const activeProvider = useStore((s) => s.activeProvider);

  // On mount, read the server-side provider rows. If a provider exists but
  // the in-memory activeProvider is null (e.g. after a page refresh), hydrate
  // it from the first row so the summary card renders. Keys live server-side
  // now, so there's no vault to probe — a provider row IS a connected key.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const remote = await listProviders();
        if (!alive) return;
        setRemoteProviders(remote);
        if (!useStore.getState().activeProvider) {
          const p = remote[0];
          if (p) {
            useStore.getState().setActiveProvider({
              providerId: p.id,
              kind: p.kind,
              label: p.label,
              keyHandle: p.key_handle ?? '',
              baseUrl: p.base_url,
              model: p.model,
              embeddingsModel: p.embeddings_model,
            });
          }
        }
      } catch {
        // server unreachable — the create form below handles it
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Seal the plaintext key (or the KeyPayload JSON envelope for kinds with
  // ``extra``) to the server's session public key. One-time, at onboarding.
  // The server opens it with its session private key and envelope-encrypts
  // the plaintext at rest. Returns the base64 enc_key_blob for createProvider.
  const sealKey = async (providerKind: ProviderKind, apiKey: string, baseUrlVal: string | null) => {
    const h = await getHealth();
    const payload: KeyPayload = {
      provider_kind: providerKind,
      api_key: apiKey,
      base_url: baseUrlVal ?? null,
      extra: null,
    };
    return sealKeyToServer(JSON.stringify(payload), h.ecdh_pub);
  };

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      if (keyVal.trim().length < 8) {
        setError(L2({ en: 'Add a key first.', ru: 'Сначала введите ключ.' }));
        return;
      }
      const handle = newKeyHandle();
      const keyTrim = keyVal.trim();
      const sealed = await sealKey(kind, keyTrim, baseUrl.trim() || null);
      const rec = await createProvider({
        kind,
        label,
        key_handle: handle,
        base_url: baseUrl.trim() || null,
        model: effectiveModel || null,
        enc_blob: null,
        enc_key_blob: sealed,
      });
      setActiveProvider({
        providerId: rec.id,
        kind: rec.kind,
        label: rec.label,
        keyHandle: rec.key_handle ?? handle,
        baseUrl: rec.base_url,
        model: rec.model,
        embeddingsModel: rec.embeddings_model,
      });
      setRemoteProviders((prev) => [...prev.filter((p) => p.id !== rec.id), rec]);
      setConnected(true);
    } catch (err) {
      // Surface the real cause — the generic message hides network vs server
      // validation failures. Log it and show the underlying message if we
      // have one.
      const detail = err instanceof Error ? err.message : String(err);
      // eslint-disable-next-line no-console
      console.error('[onboarding] connect failed:', detail);
      setError(`${t('onb.connect.fail')} (${detail})`);
    } finally {
      setBusy(false);
    }
  };

  // Add a second (or later) key. The key is sealed to the server and a new
  // ProviderRecord is POSTed. The active provider pointer does NOT move —
  // the user keeps their current selection. The form is the shared
  // `ProviderKeyForm` (also used by Settings → vault). Bedrock's AWS triplet
  // is carried in the payload's `extra` field.
  const addAnotherKey = async (values: ProviderKeyFormValues) => {
    setAddBusy(true);
    setAddError(null);
    try {
      const handle = newKeyHandle();
      const h = await getHealth();
      const payload: KeyPayload = {
        provider_kind: values.kind,
        api_key: values.apiKey,
        base_url: values.baseUrl ?? null,
        extra: values.extra ?? null,
      };
      const sealed = await sealKeyToServer(JSON.stringify(payload), h.ecdh_pub);
      const rec = await createProvider({
        kind: values.kind,
        label: values.label,
        key_handle: handle,
        base_url: values.baseUrl,
        model: values.model || null,
        embeddings_model: values.embeddingsModel,
        enc_blob: null,
        enc_key_blob: sealed,
      });
      setRemoteProviders((prev) => [...prev, rec]);
      setAddOpen(false);
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      // eslint-disable-next-line no-console
      console.error('[onboarding] add key failed:', detail);
      setAddError(`${t('onb.connect.fail')} (${detail})`);
    } finally {
      setAddBusy(false);
    }
  };

  // Wipe the server-side provider rows so the user can re-onboard and pick a
  // different provider/model/endpoint. Destructive.
  const reset = async () => {
    try {
      setBusy(true);
      setError(null);
      const remote = await listProviders();
      await Promise.all(remote.map((p) => deleteProvider(p.id)));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      console.error('[onboarding] reset failed:', detail);
      setError(detail);
    } finally {
      setActiveProvider(null);
      setRemoteProviders([]);
      setConnected(false);
      setConfirmingSwitch(false);
      setKeyVal('');
      setBaseUrl('');
      setCustomModel('');
      setModelChoice(providerMeta(kind).defaultModel);
      setBusy(false);
    }
  };

  // Persist an inline edit to the active provider. Hits PATCH /v1/providers
  // first, then mirrors the server response into the store — the UI never
  // claims a change that didn't actually persist. The key itself is not
  // touched here (it lives server-side; rotation = delete + re-add).
  // ``model`` / ``base_url`` / ``embeddings_model`` can be cleared by passing
  // ``null``; ``label`` cannot (it's required).
  const persist = async (patch: {
    label?: string;
    model?: string | null;
    base_url?: string | null;
    embeddings_model?: string | null;
  }) => {
    if (!activeProvider) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await updateProvider(activeProvider.providerId, patch);
      updateActiveProvider({
        label: updated.label,
        model: updated.model,
        baseUrl: updated.base_url,
        embeddingsModel: updated.embeddings_model,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="topbar">
        <h2>{t('onb.title')}</h2>
      </div>
      <div className="wrap">
        <div className="hero">
          <h1>{t('onb.h1')}</h1>
          <p>{t('onb.p')}</p>
        </div>
        <div className="card">
          <div className="card-title">
            {activeProvider ? t('onb.summary.title') : t('onb.c1.title')}
          </div>
          <div className="card-desc">
            {activeProvider
              ? L2({
                  en: 'Inline edit any field. Switching wipes all connected keys and starts over.',
                  ru: 'Редактируйте поля прямо здесь. Смена стирает все подключённые ключи — придётся начать заново.',
                })
              : t('onb.c1.desc')}
          </div>

          {/* STATE A: a provider is already connected. Show a summary card
              with inline-editable fields and a "Switch provider" button that
              opens the destructive confirm (calls reset()). */}
          {activeProvider && (
            <div className="summary-card">
              <div className="sc-row">
                <span className="k">{t('onb.summary.provider')}</span>
                <span className="v">
                  <strong>{providerMeta(activeProvider.kind).label}</strong>
                  {' · '}
                  {activeProvider.label}
                </span>
              </div>
              <div className="field">
                <label htmlFor="ed-label">{t('onb.label')}</label>
                <input
                  id="ed-label"
                  className="input"
                  defaultValue={activeProvider.label}
                  onBlur={(e) => {
                    const v = e.target.value.trim();
                    if (v && v !== activeProvider.label) persist({ label: v });
                  }}
                  disabled={busy}
                />
              </div>
              <div className="field">
                <label htmlFor="ed-model">{t('onb.model')}</label>
                <ModelCombobox
                  kind={activeProvider.kind}
                  value={activeProvider.model ?? ''}
                  onCommit={(v) => persist({ model: v })}
                  disabled={busy}
                />
                <div className="help">{t('onb.model.help')}</div>
              </div>
              {/* BYOK semantic memory: an embedding model id (never a key).
                  Shown only for kinds with an embeddings API. The toggle sets
                  the kind's default model / clears to null (= off); the input
                  lets the user pick a specific model. */}
              {hasEmbeddings(activeProvider.kind) && (
                <div className="field">
                  <label htmlFor="ed-embed">{t('onb.embed')}</label>
                  <div className="key-row">
                    <input
                      id="ed-embed"
                      className="input mono"
                      key={activeProvider.embeddingsModel ?? ''}
                      defaultValue={activeProvider.embeddingsModel ?? ''}
                      placeholder={_embDefault(activeProvider.kind)}
                      autoCapitalize="off"
                      autoCorrect="off"
                      spellCheck={false}
                      enterKeyHint="done"
                      onBlur={(e) => {
                        const v = e.target.value.trim();
                        const cur = activeProvider.embeddingsModel ?? null;
                        if ((v || null) !== cur) persist({ embeddings_model: v || null });
                      }}
                      disabled={busy}
                    />
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() =>
                        persist({
                          embeddings_model: activeProvider.embeddingsModel
                            ? null
                            : (_embDefault(activeProvider.kind) ?? null),
                        })
                      }
                      disabled={busy}
                    >
                      {activeProvider.embeddingsModel ? t('onb.embed.off') : t('onb.embed.on')}
                    </button>
                  </div>
                  <div className="help">{t('onb.embed.help')}</div>
                </div>
              )}
              {_hasBaseUrl(activeProvider.kind) && (
                <div className="field">
                  <label htmlFor="ed-baseurl">{t('onb.baseurl')}</label>
                  <input
                    id="ed-baseurl"
                    className="input mono"
                    defaultValue={activeProvider.baseUrl ?? ''}
                    placeholder={
                      activeProvider.kind === 'ollama' ? 'https://ollama.com' : 'https://…'
                    }
                    inputMode="url"
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    enterKeyHint="done"
                    onBlur={(e) => {
                      const v = e.target.value.trim();
                      const cur = activeProvider.baseUrl ?? null;
                      if ((v || null) !== cur) persist({ base_url: v || null });
                    }}
                    disabled={busy}
                  />
                  <div className="help">
                    {activeProvider.kind === 'ollama'
                      ? t('onb.baseurl.ollama')
                      : t('onb.baseurl.help')}
                  </div>
                </div>
              )}
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  onClick={() => {
                    setAddError(null);
                    setAddOpen(true);
                  }}
                  disabled={busy}
                >
                  {t('set.vault.add_another')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => setConfirmingSwitch(true)}
                  disabled={busy}
                >
                  {t('onb.switch')}
                </button>
              </div>
            </div>
          )}

          {/* STATE B: no provider — full onboarding form. Paste the key,
              seal it to the server, create the provider row. No passphrase,
              no vault, no restore branch. */}
          {!activeProvider && (
            <>
              <div className="provider-grid">
                {PROVIDER_ORDER.map((k) => {
                  const m = providerMeta(k);
                  return (
                    <label key={k}>
                      <input
                        type="radio"
                        name="p"
                        checked={kind === k}
                        onChange={() => setKind(k)}
                      />
                      <div className="provider-card">
                        <span className="pn">{m.label}</span>
                        <span className="pd">{L2(m.desc)}</span>
                      </div>
                    </label>
                  );
                })}
              </div>
              <div className="field">
                <label htmlFor="onb-key">{t('onb.key')}</label>
                <div className="key-row">
                  <input
                    id="onb-key"
                    className="input mono"
                    type={revealed ? 'text' : 'password'}
                    value={keyVal}
                    onChange={(e) => setKeyVal(e.target.value)}
                    aria-label={t('onb.key')}
                    placeholder="sk-…"
                    autoComplete="off"
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    inputMode="text"
                    enterKeyHint="done"
                  />
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setRevealed((r) => !r)}
                    disabled={!keyVal}
                    aria-label={revealed ? t('onb.hide') : t('onb.reveal')}
                  >
                    {revealed ? t('onb.hide') : t('onb.reveal')}
                  </button>
                </div>
                <div className="help">{t('onb.keyhelp')}</div>
              </div>
              <div className="field">
                <label htmlFor="onb-label">{t('onb.label')}</label>
                <input
                  id="onb-label"
                  className="input"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="onb-model">{t('onb.model')}</label>
                <select
                  id="onb-model"
                  className="input"
                  value={modelChoice}
                  onChange={(e) => setModelChoice(e.target.value)}
                >
                  {suggestedModels(kind).map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                  <option value={CUSTOM_SENTINEL}>{t('onb.model.custom')}</option>
                </select>
                {modelChoice === CUSTOM_SENTINEL && (
                  <input
                    className="input mono"
                    style={{ marginTop: 8 }}
                    value={customModel}
                    onChange={(e) => setCustomModel(e.target.value)}
                    placeholder={t('onb.model.placeholder')}
                    aria-label={t('onb.model')}
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    inputMode="text"
                    enterKeyHint="done"
                  />
                )}
                <div className="help">{t('onb.model.help')}</div>
              </div>
              {_hasBaseUrl(kind) && (
                <div className="field">
                  <label htmlFor="onb-baseurl">{t('onb.baseurl')}</label>
                  <input
                    id="onb-baseurl"
                    className="input mono"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder={kind === 'ollama' ? 'https://ollama.com' : 'https://…'}
                    inputMode="url"
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    enterKeyHint="done"
                  />
                  <div className="help">
                    {kind === 'ollama' ? t('onb.baseurl.ollama') : t('onb.baseurl.help')}
                  </div>
                </div>
              )}
              <button
                type="button"
                className="btn btn-primary"
                onClick={connect}
                disabled={busy}
                style={{ marginTop: 4 }}
              >
                {busy ? t('onb.connecting') : t('onb.connect')}
              </button>
            </>
          )}

          {/* Switch-provider confirm — shown only in STATE A, after the
              user clicks "Switch provider". Calls reset() on yes. */}
          {confirmingSwitch && activeProvider && (
            <div style={{ marginTop: 14 }}>
              <div className="help" style={{ marginBottom: 8 }}>
                {t('onb.reset.confirm')}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={async () => {
                    setConfirmingSwitch(false);
                    await reset();
                  }}
                  disabled={busy}
                >
                  {t('onb.reset.confirm.yes')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => setConfirmingSwitch(false)}
                  disabled={busy}
                >
                  {t('onb.reset.confirm.no')}
                </button>
              </div>
            </div>
          )}

          {/* Add-another-key modal: shown when a provider is already
              connected and the user clicks the "Add another key" button on
              the summary card. Uses the shared ProviderKeyForm. The active
              provider pointer does NOT move — the user keeps their current
              selection. */}
          <AddProviderModal
            open={addOpen}
            onClose={() => {
              if (!addBusy) setAddOpen(false);
            }}
            onSubmit={addAnotherKey}
            title={t('set.vault.add_another')}
            submitLabel={t('set.vault.add_another')}
            busy={addBusy}
          />
          {addError && (
            <div className="alt-line" style={{ color: 'var(--warn, #d4a23a)', marginTop: 10 }}>
              <span>{addError}</span>
            </div>
          )}

          {error && (
            <div className="alt-line" style={{ color: 'var(--warn, #d4a23a)', marginTop: 10 }}>
              <span>{error}</span>
            </div>
          )}
          {connected && (
            <div className="success-line">
              <svg
                aria-hidden="true"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path d="M20 6L9 17l-5-5" />
              </svg>
              <span>{t('onb.success')}</span>
            </div>
          )}
          {billing && (
            <div className="alt-line">
              <span>{t('onb.alt')}</span> <Link href="/plans">{t('onb.alt.link')}</Link>
            </div>
          )}
        </div>

        <div className="card" style={{ marginTop: 20 }}>
          <div className="card-title">{t('onb.c3.title')}</div>
          <div className="card-desc">{t('onb.c3.desc')}</div>
          <div className="persona-grid stagger">
            {PERSONAS.map((p, i) => (
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
              />
            ))}
          </div>
          <div style={{ marginTop: 20, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                startChatWith(activePersona);
                router.push('/chat');
              }}
            >
              {t('onb.start')}
            </button>
            {/* Family CTA — a non-blocking secondary. Each user is in at most
                one family, so the entry lives here and from /family (empty
                state). The check skips the CTA when the principal is already
                attached to a family (loadFamily hydrated the store on auth). */}
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => router.push('/family')}
              style={{ marginLeft: 'auto' }}
            >
              {L2({ en: 'Create a family', ru: 'Создать семью' })}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
