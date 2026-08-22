'use client';

// Family settings tabs — Invites / Therapist / Family key / Danger.
//
// This component renders ONLY the inner sub-tab strip + the active
// sub-tab body. It is mounted inside the outer /family page's
// topbar + .wrap (the Settings branch of the top-level
// Members | Therapy | Settings tab strip), so it does NOT render its
// own topbar or .wrap. The old double-chrome (Family name on top of
// "Family settings" + a "← Family" back link) is gone — the top-level
// tabs are the navigation.
//
// The owner can manage invites, customise the family therapist prompt,
// set up the family vault + LLM key, and disband/leave the family.
// Members can read the therapist prompt and the family key tab (but
// cannot edit invites / key / danger).
//
// The /family/settings and /family/vault routes are deep-link backstops
// that bounce to the new URL — see app/family/settings/page.tsx and
// app/family/vault/page.tsx.

import { ProviderKeyList } from '@/components/byok/ProviderKeyList';
import { CUSTOM_SENTINEL } from '@/components/ui/ModelCombobox';
import {
  type FamilyMemberRecord,
  type FamilyProviderRecord,
  type FamilyRecord,
  createFamilyProvider,
  deleteFamilyProvider,
  disbandFamily,
  getFamily,
  getFamilyTherapistPrompt,
  getHealth,
  leaveFamily,
  listFamilyProviders,
  listInvites,
  removeFamilyMember,
  renameFamily,
  revokeInvite,
  sendInvite,
  setFamilyTherapistPrompt,
  setFamilyUseOwnerPersonalKey,
} from '@/lib/api-client';
import { useAuthCtx } from '@/lib/auth';
import { FAM_BUILTIN_PROMPT } from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';
import {
  PROVIDER_ORDER,
  type ProviderKind,
  hasEmbeddings,
  providerMeta,
  suggestedModels,
} from '@/lib/providerCatalog';
import { resetFamilyVault } from '@/lib/reset';
import { useStore } from '@/lib/store';
import { type KeyPayload, newKeyHandle, sealKeyToServer } from '@/lib/vault';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';

type Tab = 'invites' | 'therapist' | 'key' | 'danger';

const VALID_TABS = new Set<Tab>(['invites', 'therapist', 'key', 'danger']);

// Suspense boundary so useSearchParams can suspend (Next.js 14+ requires it
// for any client component that reads searchParams). The page wrapper
// already wraps with <Suspense>; this nested boundary is the inner one.
export function FamilySettingsTabs() {
  return (
    <Suspense fallback={null}>
      <FamilySettingsTabsInner />
    </Suspense>
  );
}

function FamilySettingsTabsInner() {
  const { t, L2 } = useLang();
  const { principal } = useAuthCtx();
  const router = useRouter();
  const family = useStore((s) => s.family);
  const familyMembers = useStore((s) => s.familyMembers);
  const familyInvites = useStore((s) => s.familyInvites);
  const familyProvider = useStore((s) => s.familyProvider);
  const setFamily = useStore((s) => s.setFamily);
  const setFamilyMembers = useStore((s) => s.setFamilyMembers);
  const setFamilyInvites = useStore((s) => s.setFamilyInvites);
  const setFamilyProvider = useStore((s) => s.setFamilyProvider);
  const searchParams = useSearchParams();
  // Default for the settings sub-page: owners land on Invites (the
  // most common first action — "invite my family"), members land on
  // Therapist (the most relevant read-only surface for them).
  //
  // The inner sub-tab is read from `?subtab=` (not `?tab=`) so the
  // outer /family page can use `?tab=` for its top-level
  // (members | therapy | settings) tab strip without colliding.
  // The /family/settings backstop rewrites the legacy `?tab=` →
  // `?subtab=` on mount (see app/family/settings/page.tsx).
  const initialTab = ((): Tab => {
    const t = searchParams.get('subtab') ?? searchParams.get('tab');
    return t && VALID_TABS.has(t as Tab)
      ? (t as Tab)
      : isFamilyOwner(principal, family)
        ? 'invites'
        : 'therapist';
  })();
  const [tab, setTabState] = useState<Tab>(initialTab);
  // Inline ok/err flashes from in-tab actions (disband, invite, reset). The
  // one-shot `?flash=family_created` URL banner is handled by the parent
  // FamilySettingsScreen so it shows on the Members tab too (this component
  // only mounts on the Settings tab).
  const [flash, setFlash] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);

  const setTab = useCallback(
    (next: Tab) => {
      setTabState(next);
      // The inner sub-tab is written to `?subtab=` to coexist with the
      // outer page's `?tab=` (members | therapy | settings).
      const sp = new URLSearchParams(searchParams.toString());
      sp.set('subtab', next);
      router.replace(`/family?${sp.toString()}`);
    },
    [router, searchParams],
  );

  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteErr, setInviteErr] = useState<string | null>(null);
  const [confirmDisband, setConfirmDisband] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [busy, setBusy] = useState(false);

  const isOwner = !!family && !!principal && principal.user_id === family.owner_user_id;

  const refresh = useCallback(async () => {
    if (!principal) return;
    try {
      const [state, invites, providers] = await Promise.all([
        getFamily(),
        listInvites(),
        listFamilyProviders(),
      ]);
      setFamily(state.family);
      setFamilyMembers(state.members);
      setFamilyInvites(invites);
      setFamilyProvider(state.provider ?? providers[0] ?? null);
    } catch {
      // 404 = not in a family. Other errors are best-effort.
    }
  }, [principal, setFamily, setFamilyMembers, setFamilyInvites, setFamilyProvider]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // If the user is not in a family (e.g. landed here via a deep link),
  // bounce back to /family — the empty state is the right place to
  // create or accept an invite.
  useEffect(() => {
    if (!principal) return;
    if (!family) router.replace('/family');
  }, [principal, family, router]);

  const doInvite = async () => {
    const e = inviteEmail.trim();
    if (!/.+@.+\..+/.test(e)) {
      setInviteErr('Enter a valid email.');
      return;
    }
    setInviteErr(null);
    setBusy(true);
    try {
      await sendInvite({ email: e });
      setInviteEmail('');
      await refresh();
    } catch (err) {
      setInviteErr((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doDisband = async () => {
    setBusy(true);
    try {
      await disbandFamily();
      setFamily(null);
      setFamilyMembers([]);
      setFamilyInvites([]);
      setFamilyProvider(null);
      setConfirmDisband(false);
      setFlash({ kind: 'ok', msg: 'Family disbanded. All shared family data has been wiped.' });
      router.replace('/family');
    } catch (err) {
      setFlash({ kind: 'err', msg: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const doLeave = async () => {
    setBusy(true);
    try {
      await leaveFamily();
      setFamily(null);
      setFamilyMembers([]);
      setFamilyInvites([]);
      setFamilyProvider(null);
      setConfirmLeave(false);
      setFlash({
        kind: 'ok',
        msg: 'You left the family. Your private data has been wiped; shared family data is kept.',
      });
      router.replace('/family');
    } catch (err) {
      setFlash({ kind: 'err', msg: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const doRename = async () => {
    if (!family) return;
    const n = window.prompt('New family name', family.name);
    if (!n || n === family.name) return;
    setBusy(true);
    try {
      const f = await renameFamily({ name: n });
      setFamily(f);
    } catch (err) {
      setFlash({ kind: 'err', msg: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  if (!family) return null;

  return (
    <>
      {/* Sub-tab strip — Invites / Therapist / Family key / Danger.
          The outer topbar + .wrap are owned by FamilySettingsScreen;
          this component is mounted INSIDE that .wrap so the page has a
          single topbar and a single wrap, not two stacked. */}
      {flash && (
        <output
          className={`card fam-flash${flash.kind === 'err' ? ' fam-flash-err' : ''}`}
          style={{ marginBottom: 16 }}
        >
          <span className="dot" aria-hidden="true" />
          <span className="fam-flash-msg">{flash.msg}</span>
        </output>
      )}
      <div
        className="seg"
        role="tablist"
        style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap' }}
      >
        {(
          [
            ['invites', L2({ en: 'Invites', ru: 'Приглашения' })],
            ['therapist', t('fam.therapist_prompt.tab')],
            ['key', L2({ en: 'Family key', ru: 'Семейный ключ' })],
            ['danger', L2({ en: 'Danger zone', ru: 'Опасная зона' })],
          ] as [Tab, string][]
        ).map(([k, lbl]) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={tab === k}
            className={tab === k ? 'on' : ''}
            onClick={() => setTab(k)}
          >
            {lbl}
          </button>
        ))}
      </div>

      {tab === 'invites' && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title">{L2({ en: 'Invites', ru: 'Приглашения' })}</div>
          <div className="help" style={{ marginBottom: 10 }}>
            {L2({
              en: 'Invited members join via a one-time link in their email. The token never reappears in the UI after creation.',
              ru: 'Участники присоединяются по одноразовой ссылке из письма. Токен после создания больше не показывается.',
            })}
          </div>
          {isOwner && (
            <div className="key-row" style={{ marginBottom: 12 }}>
              <input
                className="input"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder={L2({ en: 'member@example.com', ru: 'участник@example.com' })}
                aria-label="invite email"
                style={{ maxWidth: 320 }}
                disabled={busy}
              />
              <button
                type="button"
                className="btn btn-primary"
                onClick={doInvite}
                disabled={busy || !inviteEmail.trim()}
              >
                {L2({ en: 'Send invite', ru: 'Отправить' })}
              </button>
            </div>
          )}
          {inviteErr && (
            <div className="help" style={{ marginTop: 6, color: 'var(--warn, #d4a23a)' }}>
              {inviteErr}
            </div>
          )}
          {familyInvites.length === 0 ? (
            <div className="help">{L2({ en: 'No pending invites.', ru: 'Нет приглашений.' })}</div>
          ) : (
            <div style={{ display: 'grid', gap: 6 }}>
              {familyInvites.map((inv) => (
                <div key={inv.id} className="card fam-inner fam-invite-row">
                  <span className="email">{inv.email}</span>
                  <span className="fam-spacer" />
                  <span className="sub">
                    {inv.accepted_at
                      ? L2({ en: 'Accepted', ru: 'Принято' })
                      : new Date(inv.expires_at) < new Date()
                        ? L2({ en: 'Expired', ru: 'Истёк' })
                        : L2({ en: 'Pending', ru: 'Ожидает' })}
                  </span>
                  {isOwner && !inv.accepted_at && (
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={async () => {
                        await revokeInvite(inv.id);
                        await refresh();
                      }}
                      disabled={busy}
                    >
                      {L2({ en: 'Revoke', ru: 'Отозвать' })}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'therapist' && <FamilyTherapistPromptTab isOwner={isOwner} onSaved={refresh} />}

      {tab === 'key' && (
        <FamilyKeyTab
          familyName={family.name}
          family={family}
          familyProvider={familyProvider}
          isOwner={isOwner}
          setFlash={setFlash}
          setFamily={setFamily}
          onSaved={refresh}
        />
      )}

      {tab === 'danger' && (
        <div className="card fam-danger" style={{ marginBottom: 16 }}>
          <div className="card-title">{L2({ en: 'Danger zone', ru: 'Опасная зона' })}</div>
          <div className="help" style={{ marginBottom: 8 }}>
            {L2({
              en: 'Personal (non-family) data is NOT affected by these actions.',
              ru: 'Личные данные (вне семьи) эти действия не затрагивают.',
            })}
          </div>
          {isOwner ? (
            <>
              <div className="help" style={{ marginBottom: 8 }}>
                {L2({
                  en: 'Disband family — wipes all shared family data and detaches all members. Personal data outside the family is not affected.',
                  ru: 'Роспуск семьи — стирает все общие данные и отвязывает всех участников. Личные данные вне семьи не затрагиваются.',
                })}
              </div>
              {!confirmDisband ? (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setConfirmDisband(true)}
                  disabled={busy}
                >
                  {L2({ en: 'Disband family', ru: 'Распустить семью' })}
                </button>
              ) : (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="button" className="btn btn-sm" onClick={doDisband} disabled={busy}>
                    {L2({ en: 'Yes, disband', ru: 'Да, распустить' })}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    onClick={() => setConfirmDisband(false)}
                    disabled={busy}
                  >
                    {L2({ en: 'Cancel', ru: 'Отмена' })}
                  </button>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="help" style={{ marginBottom: 8 }}>
                {L2({
                  en: 'Leave family — deletes your private disclosures in the family; shared family data is kept.',
                  ru: 'Покинуть семью — стирает ваши частные откровения; общие данные остаются.',
                })}
              </div>
              {!confirmLeave ? (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setConfirmLeave(true)}
                  disabled={busy}
                >
                  {L2({ en: 'Leave family', ru: 'Покинуть семью' })}
                </button>
              ) : (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="button" className="btn btn-sm" onClick={doLeave} disabled={busy}>
                    {L2({ en: 'Yes, leave', ru: 'Да, покинуть' })}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    onClick={() => setConfirmLeave(false)}
                    disabled={busy}
                  >
                    {L2({ en: 'Cancel', ru: 'Отмена' })}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Rename family — a family-identity action. Hidden on the Family key
          sub-tab: the add-key modal is portaled to document.body so the
          overlay can't be trapped by an ancestor stacking context, but the
          rename card is still irrelevant clutter on the key surface (and a
          prior version of it painted over the modal during the fam-rise
          entrance animation). Keep it on the other sub-tabs where it
          doesn't compete with the key form. */}
      {isOwner && tab !== 'key' && (
        <div className="card" style={{ marginBottom: 16 }}>
          <button type="button" className="btn btn-sm btn-ghost" onClick={doRename} disabled={busy}>
            {L2({ en: 'Rename family', ru: 'Переименовать семью' })}
          </button>
        </div>
      )}
    </>
  );
}

// --- Family key tab (extract: the family LLM key UI) ----------------------
//
// Hosts the family provider key card + the owner-only add/remove surface.
// The client-side family vault (passphrase + master key + IndexedDB) is
// gone — keys live server-side, envelope-encrypted. The owner adds a key
// by ECDH-sealing the plaintext to the server's session pubkey (one-time,
// at add); the server envelope-encrypts it at rest. No unlock form, no
// passphrase, no rotation. The destructive "Reset? Wipe keys" affordance
// drops the server family provider rows + clears the family vault
// metadata so the owner can re-add a key from scratch.
//
// Owner-only "use my personal key" toggle (mutually exclusive with family
// keys): when on, the family rides the owner's active personal BYOK key
// instead of a separate family_providers row — no second key entry. The
// server resolves the key from the owner's personal providers row
// (family.owner_user_id, never a client-supplied user_id — members can't
// escalate). The toggle is owner-only; non-owners see a plain notice.
function FamilyKeyTab({
  familyName,
  family,
  familyProvider,
  isOwner,
  setFlash,
  setFamily,
  onSaved,
}: {
  familyName: string;
  family: FamilyRecord;
  familyProvider: FamilyProviderRecord | null;
  isOwner: boolean;
  setFlash: (f: { kind: 'ok' | 'err'; msg: string }) => void;
  setFamily: (f: FamilyRecord | null) => void;
  onSaved: () => Promise<void> | void;
}) {
  const { t, L2 } = useLang();
  const router = useRouter();
  const familyProviders = useStore((s) => s.familyProviders);
  // The owner's currently-active personal BYOK provider. When the toggle
  // is on, the family rides this key (the owner switches it in /onboarding
  // or Settings; the family follows automatically).
  const activeProvider = useStore((s) => s.activeProvider);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetTyped, setResetTyped] = useState('');
  const [resetErr, setResetErr] = useState<string | null>(null);
  const [resetBusy, setResetBusy] = useState(false);
  const [toggleBusy, setToggleBusy] = useState(false);

  const count = familyProviders.length;
  const hasKey = !!familyProvider?.key_handle;
  const usePersonal = !!family.use_owner_personal_key;
  // The toggle only does something when the owner has a personal key to
  // ride. Without one, the checkbox is disabled and the owner is sent to
  // /onboarding to add one first.
  const hasPersonalKey = !!activeProvider?.keyHandle;

  const doReset = async () => {
    setResetErr(null);
    setResetBusy(true);
    try {
      await resetFamilyVault();
      setResetOpen(false);
      setResetTyped('');
      await onSaved();
      setFlash({ kind: 'ok', msg: 'Family keys wiped. Add a new family LLM API key below.' });
    } catch (e) {
      setResetErr(
        t('fam.vault.reset.fail', { message: e instanceof Error ? e.message : String(e) }),
      );
    } finally {
      setResetBusy(false);
    }
  };

  const canConfirmReset = resetTyped.trim() === familyName.trim() && familyName.length > 0;

  const toggleUsePersonal = async (next: boolean) => {
    setToggleBusy(true);
    try {
      const updated = await setFamilyUseOwnerPersonalKey(next);
      setFamily(updated);
      await onSaved();
    } catch (e) {
      setFlash({
        kind: 'err',
        msg: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setToggleBusy(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-title">{L2({ en: 'Family key', ru: 'Семейный ключ' })}</div>

      {/* Status line — mirrors the personal Key vault tab. Two modes:
          toggle on → "Using the owner's personal key"; toggle off → the
          family-key count (or the "no key yet" call-to-action). */}
      {usePersonal ? (
        <p className="set-stat">
          {L2({
            en: 'Using the owner’s personal key',
            ru: 'Используется личный ключ владельца',
          })}
        </p>
      ) : count === 0 ? (
        <p className="card-desc" style={{ marginBottom: 8 }}>
          {L2({
            en: 'No family key yet. Add your own API key so family members can chat.',
            ru: 'Семейного ключа ещё нет. Добавьте свой ключ API, чтобы члены семьи могли общаться.',
          })}
        </p>
      ) : (
        <p className="set-stat tnum">
          <b>{count}</b>{' '}
          {L2({
            en: `key${count === 1 ? '' : 's'} connected`,
            ru: 'ключей подключено',
          })}
        </p>
      )}

      {isOwner ? (
        <>
          {/* Owner-only "use my personal key" toggle — mutually exclusive
              with family keys. Sits above the family add form. When on,
              the family add form + list are hidden (the family rides the
              owner's active personal key). Without a personal key, the
              checkbox is disabled and the owner is pointed to /onboarding. */}
          <div
            className="card fam-inner"
            style={{ marginBottom: 12 }}
            data-family-use-personal={usePersonal ? 'on' : 'off'}
          >
            <label className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
              <input
                type="checkbox"
                checked={usePersonal}
                onChange={(e) => void toggleUsePersonal(e.target.checked)}
                disabled={toggleBusy || !hasPersonalKey}
                data-family-use-personal-checkbox="1"
              />
              <span>
                <span className="set-stat" style={{ display: 'block' }}>
                  {t('fam.key.use_personal.label')}
                </span>
                <span className="help">{t('fam.key.use_personal.help')}</span>
                {!hasPersonalKey && (
                  <span className="help" style={{ display: 'block', marginTop: 4 }}>
                    {t('fam.key.use_personal.no_personal_key')}{' '}
                    <a
                      href="/onboarding"
                      onClick={(e) => {
                        e.preventDefault();
                        router.push('/onboarding');
                      }}
                    >
                      /onboarding
                    </a>
                  </span>
                )}
                {usePersonal && hasPersonalKey && activeProvider && (
                  <span className="help" style={{ display: 'block', marginTop: 4 }}>
                    {t('fam.key.use_personal.in_use')}{' '}
                    <span className="sub">
                      {activeProvider.label || activeProvider.kind} · {activeProvider.kind}
                    </span>
                  </span>
                )}
              </span>
            </label>
          </div>

          {/* When the toggle is on, the family key list + add form are
              unused (the family rides the personal key) — hide them. */}
          {!usePersonal && (
            <FamilyKeyManager
              onChanged={async () => {
                await onSaved();
              }}
            />
          )}
        </>
      ) : (
        // Non-owner: the family uses the owner's personal key (toggle on)
        // or a family key (toggle off). Members can read either but never
        // add/change; the dead-greyed form is gone (Phase 2 #8).
        <div className="card fam-inner">
          <div className="help">
            {usePersonal
              ? t('fam.key.use_personal.nonowner_notice_on')
              : t('fam.key.form.nonowner_notice')}
          </div>
        </div>
      )}

      {/* Owner-only destructive reset affordance. Only meaningful when the
          family is in family-key mode (toggle off) AND a family key exists
          — the personal-key mode has nothing to wipe here. The confirm
          types the family name verbatim (matches the disband/leave confirm
          pattern) so an accidental click can't wipe the family keys. */}
      {isOwner && hasKey && !usePersonal && !resetOpen && (
        <div style={{ marginTop: 12 }}>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => {
              setResetOpen(true);
              setResetTyped('');
              setResetErr(null);
            }}
            data-family-vault-action="forgot"
          >
            {L2({ en: 'Reset? Wipe family keys', ru: 'Сбросить? Стереть семейные ключи' })}
          </button>
        </div>
      )}
      {resetOpen && (
        <div className="family-vault-inline-form" style={{ marginTop: 8 }}>
          <div className="help">
            {L2({
              en: 'Type the family name to wipe all family provider keys on the server. You will need to re-add a key to chat again.',
              ru: 'Введите название семьи, чтобы стереть все семейные ключи провайдера на сервере. Чтобы продолжить чат, нужно будет заново добавить ключ.',
            })}
          </div>
          <div className="row">
            <input
              className="input"
              value={resetTyped}
              onChange={(e) => setResetTyped(e.target.value)}
              placeholder={familyName}
              aria-label={L2({ en: 'Type the family name', ru: 'Введите название семьи' })}
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="done"
              disabled={resetBusy}
            />
            <button
              type="button"
              className="btn btn-sm"
              onClick={doReset}
              disabled={resetBusy || !canConfirmReset}
            >
              {L2({ en: 'Wipe & reset', ru: 'Стереть и сбросить' })}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => {
                setResetOpen(false);
                setResetTyped('');
                setResetErr(null);
              }}
              disabled={resetBusy}
            >
              {L2({ en: 'Cancel', ru: 'Отмена' })}
            </button>
          </div>
          {resetErr && <div className="err">{resetErr}</div>}
        </div>
      )}

      {/* Neutral key-storage note + docs pointer. The full disclosure (the
          server holds the DEK and can decrypt at reply time — not
          zero-knowledge) lives in SECURITY.md, not on this surface. */}
      <p className="help" style={{ marginTop: 12 }}>
        {L2({
          en: 'The family API key is encrypted in transit and at rest on the server.',
          ru: 'Семейный ключ API шифруется при передаче и хранится зашифрованным на сервере.',
        })}
      </p>
    </div>
  );
}

function isFamilyOwner(
  principal: { user_id: string } | null,
  family: { owner_user_id: string } | null,
): boolean {
  return !!family && !!principal && principal.user_id === family.owner_user_id;
}

// Multi-key family BYOK surface — owner-only. A list of existing family
// providers (one row per key) with a per-row remove, plus an inline "add a
// family key" form. The add form is a verbatim copy of the personal
// onboarding STATE B connect form (provider radio grid, key + reveal, label,
// model <select> with custom, base URL, single submit) — no embeddings /
// AWS / get-key link, by design. The encryption + API call stay here so the
// family flow (its own endpoint + shared key surface) is self-contained.
//
// The family key surface is SHARED across all members: a member's turn
// resolves which key to use the same way a personal turn does (via the
// active pointer in the store, hydrated from `listFamilyProviders`).
// Non-owners see a read-only notice instead (handled by the parent tab).
function FamilyKeyManager({
  onChanged,
}: {
  onChanged: () => Promise<void> | void;
}) {
  const { t, L2 } = useLang();
  const familyProviders = useStore((s) => s.familyProviders);
  const setFamilyProviders = useStore((s) => s.setFamilyProviders);
  const setActiveFamilyProviderId = useStore((s) => s.setActiveFamilyProviderId);
  const setFamilyProvider = useStore((s) => s.setFamilyProvider);
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);

  // Inline add form — mirrors onboarding STATE B exactly. Same state shape,
  // same i18n keys, same provider-grid + model <select> + base-URL branch.
  const [kind, setKind] = useState<ProviderKind>('openai');
  const [keyVal, setKeyVal] = useState('');
  const [label, setLabel] = useState('Family OpenAI');
  const [modelChoice, setModelChoice] = useState<string>(providerMeta('openai').defaultModel);
  const [customModel, setCustomModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  // Optional embedding model for BYOK semantic memory — mirrors the
  // onboarding STATE A embeddings field. Empty by default (off); the toggle
  // flips it to the kind's default / clears it. Stored on the family provider
  // row and sent on family turns (ChatScreen already wires embeddings_model).
  const [embeddingsModel, setEmbeddingsModel] = useState('');
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // When the provider kind changes, reset the model picker to that kind's
  // default curated model and clear any custom entry — same as onboarding.
  useEffect(() => {
    setModelChoice(providerMeta(kind).defaultModel);
    setCustomModel('');
    setEmbeddingsModel('');
  }, [kind]);

  const effectiveModel = modelChoice === CUSTOM_SENTINEL ? customModel.trim() : modelChoice;
  // Same base-URL visibility rule as onboarding's `_hasBaseUrl`: hidden for
  // fixed-origin gateways (AIHubMix) and for the AWS credential shape.
  const showBaseUrl = kind !== 'aihubmix' && providerMeta(kind).credentialShape !== 'aws';

  // Add a family key: ECDH-seal the plaintext key to the server's session
  // pubkey (one-time) and create a new FamilyProviderRecord. The server
  // envelope-encrypts the key at rest under its DEK. The new key becomes
  // active by default — there's no per-user active pointer for the family
  // (one shared surface), so "the one I just added" is the natural pick.
  const addFamilyKey = async () => {
    setBusy(true);
    setError(null);
    try {
      if (keyVal.trim().length < 8) {
        setError(L2({ en: 'Add a key first.', ru: 'Сначала введите ключ.' }));
        return;
      }
      const h = await getHealth();
      const payload: KeyPayload = {
        provider_kind: kind,
        api_key: keyVal.trim(),
        base_url: baseUrl.trim() || null,
        extra: null,
      };
      const sealed = await sealKeyToServer(JSON.stringify(payload), h.ecdh_pub);
      const key_handle = newKeyHandle();
      const created = await createFamilyProvider({
        kind,
        label,
        base_url: baseUrl.trim() || null,
        key_handle,
        model: effectiveModel || null,
        embeddings_model: hasEmbeddings(kind) ? embeddingsModel.trim() || null : null,
        enc_key_blob: sealed,
      });
      setFamilyProviders([...familyProviders, created]);
      setActiveFamilyProviderId(created.id);
      setFamilyProvider(created);
      // Reset the key-only fields so the user can add another key without
      // re-typing the label/model. Mirrors onboarding's post-connect clear.
      setKeyVal('');
      setBaseUrl('');
      setCustomModel('');
      setEmbeddingsModel('');
      setModelChoice(providerMeta(kind).defaultModel);
      await onChanged();
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      // eslint-disable-next-line no-console
      console.error('[family-key] add failed:', detail);
      setError(`${t('onb.connect.fail')} (${detail})`);
    } finally {
      setBusy(false);
    }
  };

  // Server delete — the key lives server-side, so deleting the row is the
  // whole destructive action. No local IndexedDB blob to wipe (the client
  // vault is gone).
  const removeFamilyKey = async (id: string) => {
    await deleteFamilyProvider(id);
    const remaining = familyProviders.filter((p) => p.id !== id);
    setFamilyProviders(remaining);
    if (remaining[0]) {
      setActiveFamilyProviderId(remaining[0].id);
      setFamilyProvider(remaining[0]);
    } else {
      setActiveFamilyProviderId(null);
      setFamilyProvider(null);
    }
    await onChanged();
  };

  const count = familyProviders.length;
  const submitLabel = count === 0 ? t('fam.key.add_key') : t('fam.key.add_another');

  return (
    <div>
      <ProviderKeyList
        familyProviders={familyProviders}
        activeProviderId={undefined}
        onRemove={(id) => setConfirmingRemove(id)}
        busy={busy}
      />

      {confirmingRemove && (
        <div className="set-confirm" style={{ marginTop: 8 }}>
          <span className="help">
            {L2({ en: 'Remove this family key?', ru: 'Удалить ключ семьи?' })}
          </span>
          <button
            type="button"
            className="btn btn-sm btn-danger-ghost"
            onClick={async () => {
              const id = confirmingRemove;
              setConfirmingRemove(null);
              if (id) await removeFamilyKey(id);
            }}
          >
            {L2({ en: 'Yes, remove', ru: 'Да, удалить' })}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => setConfirmingRemove(null)}
          >
            {L2({ en: 'Cancel', ru: 'Отмена' })}
          </button>
        </div>
      )}

      {/* Inline add form — verbatim copy of the onboarding STATE B connect
          form. Rendered directly in the card (no modal), so the family key
          addition looks and behaves identically to the personal one. */}
      <div className="family-vault-inline-form" style={{ marginTop: 12 }}>
        <div className="provider-grid">
          {PROVIDER_ORDER.map((k) => {
            const m = providerMeta(k);
            return (
              <label key={k}>
                <input
                  type="radio"
                  name="fam-p"
                  checked={kind === k}
                  onChange={() => setKind(k)}
                  disabled={busy}
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
          <label htmlFor="fam-onb-key">{t('onb.key')}</label>
          <div className="key-row">
            <input
              id="fam-onb-key"
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
              disabled={busy}
            />
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setRevealed((r) => !r)}
              disabled={!keyVal || busy}
              aria-label={revealed ? t('onb.hide') : t('onb.reveal')}
            >
              {revealed ? t('onb.hide') : t('onb.reveal')}
            </button>
          </div>
          <div className="help">{t('onb.keyhelp')}</div>
        </div>
        <div className="field">
          <label htmlFor="fam-onb-label">{t('onb.label')}</label>
          <input
            id="fam-onb-label"
            className="input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            disabled={busy}
          />
        </div>
        <div className="field">
          <label htmlFor="fam-onb-model">{t('onb.model')}</label>
          <select
            id="fam-onb-model"
            className="input"
            value={modelChoice}
            onChange={(e) => setModelChoice(e.target.value)}
            disabled={busy}
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
              disabled={busy}
            />
          )}
          <div className="help">{t('onb.model.help')}</div>
        </div>
        {/* Optional embedding model — same UI as the onboarding STATE A
            embeddings field (input + on/off toggle). Off by default; the
            toggle flips to the kind's default / clears. Only kinds with an
            embeddings API show it. Stored on the family provider and used
            by family turns (ChatScreen sends embeddings_model). */}
        {hasEmbeddings(kind) && (
          <div className="field">
            <label htmlFor="fam-onb-embed">{t('onb.embed')}</label>
            <div className="key-row">
              <input
                id="fam-onb-embed"
                className="input mono"
                value={embeddingsModel}
                onChange={(e) => setEmbeddingsModel(e.target.value)}
                placeholder={providerMeta(kind).embeddingsDefault ?? ''}
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                enterKeyHint="done"
                disabled={busy}
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  setEmbeddingsModel((prev) =>
                    prev ? '' : (providerMeta(kind).embeddingsDefault ?? ''),
                  )
                }
                disabled={busy}
              >
                {embeddingsModel ? t('onb.embed.off') : t('onb.embed.on')}
              </button>
            </div>
            <div className="help">{t('onb.embed.help')}</div>
          </div>
        )}
        {showBaseUrl && (
          <div className="field">
            <label htmlFor="fam-onb-baseurl">{t('onb.baseurl')}</label>
            <input
              id="fam-onb-baseurl"
              className="input mono"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={kind === 'ollama' ? 'https://ollama.com' : 'https://…'}
              inputMode="url"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="done"
              disabled={busy}
            />
            <div className="help">
              {kind === 'ollama' ? t('onb.baseurl.ollama') : t('onb.baseurl.help')}
            </div>
          </div>
        )}
        <button
          type="button"
          className="btn btn-primary"
          onClick={addFamilyKey}
          disabled={busy}
          style={{ marginTop: 4 }}
        >
          {busy ? t('onb.connecting') : submitLabel}
        </button>
      </div>

      {error && (
        <div className="alt-line" style={{ marginTop: 8, color: 'var(--warn, #d4a23a)' }}>
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}

// --- Family therapist prompt tab (owner-write, member-read) ----------------
//
// Mirrors the existing custom-persona pattern (PersonaScreen.tsx): four
// optional text sections the owner composes, with the hard-coded
// "Disclose, don't perform" footer appended at compose time so the owner
// can never drop the safety line. Members see the body + audit read-only.
//
// Why client-side "Family context" pre-fill: the server already returns
// ``familyMembers``; the owner can edit freely. Zero new contract surface.

const FAM_PROMPT_SAFETY_FOOTER = "Disclose, don't perform. Never claim feelings you don't have.";

const FAM_PROMPT_MAX_BODY = 8_000;

function FamilyTherapistPromptTab({
  isOwner,
  onSaved,
}: {
  isOwner: boolean;
  onSaved: () => Promise<void> | void;
}) {
  const { t, lang } = useLang();
  const familyMembers = useStore((s) => s.familyMembers);
  const stored = useStore((s) => s.familyTherapistPrompt);
  const setStored = useStore((s) => s.setFamilyTherapistPrompt);

  // Local form state. Seeded from the stored body when the owner first
  // opens the form; for members the form is read-only and we don't track
  // local state.
  const seededDefault = useMemo(() => {
    const ctx = familyMembers.map((m) => `${m.family_display_name} (${m.relation})`).join(', ');
    return { focus: '', rules: '', context: ctx, approach: '' };
  }, [familyMembers]);

  const [focus, setFocus] = useState(seededDefault.focus);
  const [rules, setRules] = useState(seededDefault.rules);
  const [context, setContext] = useState(seededDefault.context);
  const [approach, setApproach] = useState(seededDefault.approach);
  const [seeded, setSeeded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (seeded) return;
    if (stored === undefined) return;
    if (stored === null) {
      setSeeded(true);
      return;
    }
    setSeeded(true);
  }, [stored, seeded]);

  const composePrompt = (f: string, r: string, c: string, a: string): string => {
    const parts: string[] = [];
    const fT = f.trim();
    const rT = r.trim();
    const cT = c.trim();
    const aT = a.trim();
    if (fT) parts.push(`Session focus: ${fT}`);
    if (rT) parts.push(`Family rules: ${rT}`);
    if (cT) parts.push(`Family context: ${cT}`);
    if (aT) parts.push(`Approach: ${aT}`);
    parts.push(FAM_PROMPT_SAFETY_FOOTER);
    return parts.join('\n');
  };

  const onSave = async () => {
    setErr(null);
    const body = composePrompt(focus, rules, context, approach);
    if (body.length > FAM_PROMPT_MAX_BODY) {
      setErr(t('fam.therapist_prompt.error.body_too_long'));
      return;
    }
    setSaving(true);
    try {
      const next = await setFamilyTherapistPrompt({ body });
      setStored(next);
      setSavedAt(Date.now());
      await onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onReset = async () => {
    setErr(null);
    setSaving(true);
    try {
      const next = await setFamilyTherapistPrompt({ body: null });
      setStored(next);
      setSavedAt(Date.now());
      setFocus('');
      setRules('');
      setContext(seededDefault.context);
      setApproach('');
      await onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const auditName = stored?.set_by_display_name ?? null;
  const auditAt = stored?.set_at ?? null;
  const bodyForPreview =
    stored?.body && stored.body.length > 0
      ? stored.body
      : composePrompt(focus, rules, context, approach);
  const composedPreview = bodyForPreview;

  // Localized builtin fallback (mirrored from the server registry).
  const builtin = FAM_BUILTIN_PROMPT[lang as 'en' | 'ru'] ?? FAM_BUILTIN_PROMPT.en;
  const displayedBody = stored?.body && stored.body.length > 0 ? stored.body : builtin;

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-title">{t('fam.therapist_prompt.title')}</div>
      <div className="help" style={{ marginBottom: 10 }}>
        {t('fam.therapist_prompt.sub')}
      </div>
      {!isOwner && (
        <div className="card fam-inner" style={{ marginBottom: 12 }}>
          <pre className="fam-preview" data-therapist-prompt-preview>
            {displayedBody}
          </pre>
        </div>
      )}
      {!isOwner && (
        <div className="help" data-therapist-prompt-audit style={{ marginTop: 8 }}>
          {auditName && auditAt
            ? t('fam.therapist_prompt.audit')
                .replace('{name}', auditName)
                .replace('{date}', new Date(auditAt).toLocaleString())
            : t('fam.therapist_prompt.audit.builtin')}
        </div>
      )}

      {isOwner && (
        <div className="fam-fieldstack">
          <div>
            <label
              htmlFor="fam-therapist-focus"
              style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}
            >
              {t('fam.therapist_prompt.section.focus')}
            </label>
            <div className="help" style={{ marginBottom: 6 }}>
              {t('fam.therapist_prompt.section.focus.tip')}
            </div>
            <textarea
              id="fam-therapist-focus"
              data-therapist-section="focus"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              rows={2}
              style={{ width: '100%', resize: 'vertical' }}
              placeholder={t('fam.therapist_prompt.section.focus.placeholder')}
              disabled={saving}
            />
          </div>
          <div>
            <label
              htmlFor="fam-therapist-rules"
              style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}
            >
              {t('fam.therapist_prompt.section.rules')}
            </label>
            <div className="help" style={{ marginBottom: 6 }}>
              {t('fam.therapist_prompt.section.rules.tip')}
            </div>
            <textarea
              id="fam-therapist-rules"
              data-therapist-section="rules"
              value={rules}
              onChange={(e) => setRules(e.target.value)}
              rows={3}
              style={{ width: '100%', resize: 'vertical' }}
              placeholder={t('fam.therapist_prompt.section.rules.placeholder')}
              disabled={saving}
            />
          </div>
          <div>
            <label
              htmlFor="fam-therapist-context"
              style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}
            >
              {t('fam.therapist_prompt.section.context')}
            </label>
            <div className="help" style={{ marginBottom: 6 }}>
              {t('fam.therapist_prompt.section.context.tip')}
            </div>
            <textarea
              id="fam-therapist-context"
              data-therapist-section="context"
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={3}
              style={{ width: '100%', resize: 'vertical' }}
              disabled={saving}
            />
          </div>
          <div>
            <label
              htmlFor="fam-therapist-approach"
              style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}
            >
              {t('fam.therapist_prompt.section.approach')}
            </label>
            <div className="help" style={{ marginBottom: 6 }}>
              {t('fam.therapist_prompt.section.approach.tip')}
            </div>
            <textarea
              id="fam-therapist-approach"
              data-therapist-section="approach"
              value={approach}
              onChange={(e) => setApproach(e.target.value)}
              rows={3}
              style={{ width: '100%', resize: 'vertical' }}
              placeholder={t('fam.therapist_prompt.section.approach.placeholder')}
              disabled={saving}
            />
          </div>

          <div>
            <div style={{ fontWeight: 500, marginBottom: 4 }} data-therapist-preview-title>
              {t('fam.therapist_prompt.preview.title')}
            </div>
            <div className="help" style={{ marginBottom: 6 }}>
              {t('fam.therapist_prompt.preview.help')}
            </div>
            <div className="card fam-inner">
              <pre className="fam-preview" data-therapist-prompt-preview>
                {composedPreview}
              </pre>
            </div>
          </div>

          {err && (
            <div className="help" style={{ color: 'var(--danger)' }}>
              {err}
            </div>
          )}
          {savedAt && !err && (
            <div className="help" data-therapist-saved>
              {t('fam.therapist_prompt.saved')}
            </div>
          )}

          <div className="fam-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={onSave}
              disabled={saving}
              data-therapist-save
            >
              {t('fam.therapist_prompt.save')}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={onReset}
              disabled={saving}
              data-therapist-reset
            >
              {t('fam.therapist_prompt.clear')}
            </button>
          </div>

          {auditName && auditAt && (
            <div className="help fam-audit" data-therapist-prompt-audit>
              {t('fam.therapist_prompt.audit')
                .replace('{name}', auditName)
                .replace('{date}', new Date(auditAt).toLocaleString())}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Suppress unused warning for the FamilyMemberRecord type — it's used
// implicitly via the familyMembers map in FamilyTherapistPromptTab.
type _Keep = FamilyMemberRecord;
