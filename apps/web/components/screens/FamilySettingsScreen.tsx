'use client';

// Family page — /family.
//
// Hosts the three top-level tabs (Members | Therapy | Settings). The
// "Settings" tab is a thin wrapper around <FamilySettingsTabs />,
// which keeps its own sub-tab strip (Invites / Therapist / Family key
// / Danger). The split mirrors the in-page tab pattern used in
// SettingsScreen.tsx:110, 129 and FamilySettingsTabs.tsx:281-305 — a
// `.seg` strip is the project's standard for in-page navigation, NOT a
// corner link.
//
// The empty state ("create a family" + pending-invite banner) is shown
// when the user is signed in but not in a family. The tab strip is
// hidden in that case (there's no Therapy or Settings to navigate to
// before creating the family).
//
// The client-side family vault is gone — BYOK keys live server-side,
// envelope-encrypted. The Therapy CTA just opens the family chat when
// a family provider row exists; otherwise it links to the Family key
// sub-tab so the owner can add one. No inline passphrase form.
//
// The settings sub-tab URL state uses `?subtab=...` so it coexists
// with the outer `?tab=...` (see FamilySettingsTabs.tsx for the
// matching change). The legacy /family/settings and /family/vault
// routes are kept as deep-link backstops that rewrite to the new URL.

import { FamilyPrimarySkeleton } from '@/components/Skeleton';
import { FamilySettingsTabs } from '@/components/screens/FamilySettingsTabs';
import {
  type FamilyMemberRecord,
  createFamily,
  getFamily,
  getFamilyTherapistPrompt,
  listFamilyProviders,
  listInvites,
} from '@/lib/api-client';
import { useAuthCtx } from '@/lib/auth';
import { useLang } from '@/lib/i18n';
import { useStore } from '@/lib/store';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState } from 'react';

type TopTab = 'members' | 'therapy' | 'settings';
const VALID_TOP_TABS = new Set<TopTab>(['members', 'therapy', 'settings']);

export function FamilySettingsScreen() {
  return (
    <Suspense fallback={<FamilyPrimarySkeleton />}>
      <FamilySettingsScreenInner />
    </Suspense>
  );
}

function FamilySettingsScreenInner() {
  const { principal, loading: authLoading } = useAuthCtx();
  if (authLoading || !principal) {
    return <FamilyPrimarySkeleton />;
  }
  return <FamilyPrimaryScreen principal={principal} />;
}

function FamilyPrimaryScreen({
  principal,
}: {
  principal: {
    user_id: string;
    email?: string | null;
    family_id?: string | null;
    family_role?: string | null;
  };
}) {
  const { t, L2 } = useLang();
  const router = useRouter();
  const searchParams = useSearchParams();
  const family = useStore((s) => s.family);
  const familyMembers = useStore((s) => s.familyMembers);
  const familyProvider = useStore((s) => s.familyProvider);
  const setFamily = useStore((s) => s.setFamily);
  const setFamilyMembers = useStore((s) => s.setFamilyMembers);
  const setFamilyInvites = useStore((s) => s.setFamilyInvites);
  const setFamilyProvider = useStore((s) => s.setFamilyProvider);

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isOwner = !!family && principal.user_id === family.owner_user_id;

  // Top-level tab from `?tab=`. Default = members.
  const topTab: TopTab = (() => {
    const t = searchParams.get('tab');
    return t && VALID_TOP_TABS.has(t as TopTab) ? (t as TopTab) : 'members';
  })();

  const setTopTab = useCallback(
    (next: TopTab) => {
      const sp = new URLSearchParams(searchParams.toString());
      sp.set('tab', next);
      // Clear `?subtab=` when leaving the Settings tab so we don't
      // carry stale sub-tab state into a different top-level tab.
      if (next !== 'settings') sp.delete('subtab');
      const qs = sp.toString();
      router.replace(qs ? `/family?${qs}` : '/family');
    },
    [router, searchParams],
  );

  // One-shot `?flash=family_created` banner — shown after createFamily
  // redirects to /family?tab=members&flash=family_created. Lives here (not in
  // FamilySettingsTabs) so it renders on the Members tab, which is where the
  // redirect lands. The banner links to the Family key sub-tab for when the
  // owner is ready to add a key.
  const familyFlash =
    searchParams.get('flash') === 'family_created'
      ? L2({
          en: 'Family created. Invite members, then add a family key when you’re ready.',
          ru: 'Семья создана. Пригласите участников, затем добавьте семейный ключ, когда будете готовы.',
        })
      : null;
  useEffect(() => {
    if (!searchParams.get('flash')) return;
    const sp = new URLSearchParams(searchParams.toString());
    sp.delete('flash');
    const qs = sp.toString();
    router.replace(qs ? `/family?${qs}` : '/family');
  }, [searchParams, router]);

  const refresh = useCallback(async () => {
    try {
      const [state, invites, providers, _therapistPrompt] = await Promise.all([
        getFamily(),
        listInvites(),
        listFamilyProviders(),
        getFamilyTherapistPrompt().catch(() => null),
      ]);
      setFamily(state.family);
      setFamilyMembers(state.members);
      setFamilyInvites(invites);
      setFamilyProvider(state.provider ?? providers[0] ?? null);
    } catch {
      // 404 = not in a family. Other errors are best-effort.
    }
  }, [setFamily, setFamilyMembers, setFamilyInvites, setFamilyProvider]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Detect a pending family invite that the /family/accept page stashed in a
  // short-lived cookie before redirecting an unauthenticated user to /login.
  // The banner above the create form lets a user pick up a half-completed
  // invite without re-finding the email link.
  const [pendingInvite, setPendingInvite] = useState<string | null>(null);
  useEffect(() => {
    const read = () => {
      if (typeof document === 'undefined') return;
      const raw = document.cookie
        .split(';')
        .map((s) => s.trim())
        .find((s) => s.startsWith('family_invite_token='));
      if (!raw) {
        setPendingInvite(null);
        return;
      }
      const v = raw.slice('family_invite_token='.length);
      setPendingInvite(v ? decodeURIComponent(v) : null);
    };
    read();
    window.addEventListener('focus', read);
    return () => window.removeEventListener('focus', read);
  }, []);

  const doCreate = async () => {
    const n = name.trim();
    if (n.length < 1) {
      setNameErr('Name is required.');
      return;
    }
    setNameErr(null);
    setBusy(true);
    try {
      const f = await createFamily({ name: n });
      setFamily(f);
      setName('');
      setCreating(false);
      // Phase 2 #7: land a freshly-minted owner on Members with a one-shot
      // "family created" flash — they invite members first, then add a family
      // key when they're ready (the flash links to the key sub-tab). We no
      // longer funnel straight into the key form before the owner has chatted
      // or invited anyone. The flash is consumed by FamilySettingsTabs.
      router.replace('/family?tab=members&flash=family_created');
    } catch (e) {
      setNameErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onOpenFamilyChat = () => {
    // No client-side vault to unlock anymore — keys live server-side.
    // If no family provider row exists, bounce to the Family key sub-tab
    // so the owner can add one. Otherwise open a new family convo +
    // switch persona in one step. startChatWith (NOT setActivePersona)
    // fixes the bug where the old personal convo re-appears on /chat —
    // the activeConvoId is updated alongside activePersonaId so the
    // ChatScreen sees a coherent (convo, persona) pair.
    if (!familyProvider?.key_handle) {
      router.push('/family?tab=settings&subtab=key');
      return;
    }
    useStore.getState().startChatWith('fam');
    router.push('/chat');
  };

  // ---- Empty: create a family ----
  // No tab strip in the empty state — the user must create the family
  // first; the other tabs (Therapy, Settings) are meaningless without one.
  if (!family) {
    return (
      <>
        <div className="wrap fam-wrap">
          <div className="pagehead fam-head">
            <div className="pagehead__row">
              <div>
                <h1>{L2({ en: 'Family', ru: 'Семья' })}</h1>
                <p className="lede">{t('fam.lede')}</p>
              </div>
            </div>
          </div>

          {pendingInvite && (
            <div className="card fam-banner" style={{ maxWidth: 540, marginBottom: 16 }}>
              <div className="card-title">
                {L2({
                  en: 'You have a pending family invite',
                  ru: 'У вас есть приглашение в семью',
                })}
              </div>
              <div className="help" style={{ marginBottom: 12 }}>
                {L2({
                  en: 'Open the link in your email, or click below to accept the invite you started earlier. The token is valid for 30 minutes.',
                  ru: 'Откройте ссылку из письма или нажмите кнопку ниже, чтобы принять приглашение. Токен действителен 30 минут.',
                })}
              </div>
              <Link href="/family/accept" className="btn btn-primary">
                {L2({ en: 'Open the invite', ru: 'Открыть приглашение' })}
              </Link>
            </div>
          )}
          <div className="card fam-mw540">
            <div className="card-title">{L2({ en: 'Create a family', ru: 'Создать семью' })}</div>
            <div className="help" style={{ marginBottom: 12 }}>
              {L2({
                en: 'You become the family owner. You can invite up to three more members by email. The family uses its own server-side envelope-encrypted API key — separate from your personal one.',
                ru: 'Вы станете владельцем семьи. Можно пригласить до трёх членов по email. У семьи свой собственный ключ API в конвертном шифровании на сервере — отдельный от личного.',
              })}
            </div>
            <div className="key-row">
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={L2({ en: 'Family name', ru: 'Название семьи' })}
                aria-label="family name"
                style={{ maxWidth: 280 }}
                disabled={busy}
              />
              <button
                type="button"
                className="btn btn-primary"
                onClick={doCreate}
                disabled={busy || !name.trim()}
              >
                {L2({ en: 'Create', ru: 'Создать' })}
              </button>
            </div>
            {nameErr && (
              <div className="help" style={{ marginTop: 8, color: 'var(--warn, #d4a23a)' }}>
                {nameErr}
              </div>
            )}
            <hr style={{ border: 0, borderTop: '1px solid var(--border)', margin: '20px 0' }} />
            <div className="help">
              {L2({
                en: 'A family lets a small household share one companion. Each member keeps their own private 1:1 chats, and there is one shared "joint" thread everyone in the family can read and write. The joint thread runs on a single shared API key; each member can also connect their own key for their private chats. Keys are encrypted on the server.',
                ru: 'Семья позволяет небольшому дому делить одного компаньона. У каждого участника остаются личные чаты 1:1, а также есть одна общая «совместная» ветка, которую видят и пишут все члены семьи. Совместная ветка работает на одном общем API-ключе, а для личных чатов каждый может подключить свой ключ. Ключи шифруются на сервере.',
              })}
            </div>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="wrap fam-wrap">
        <div className="pagehead fam-head">
          <div className="pagehead__row">
            <div>
              <h1>{family.name}</h1>
              <p className="lede">{t('fam.lede')}</p>
            </div>
            <span className="badge badge--mute">
              {isOwner
                ? L2({ en: 'You are the owner', ru: 'Вы владелец' })
                : L2({ en: 'Member', ru: 'Участник' })}
            </span>
          </div>
        </div>

        <div className="comp-row" aria-label={L2({ en: 'Family composition', ru: 'Состав семьи' })}>
          <div className="avatars" aria-hidden="true">
            {familyMembers.slice(0, 4).map((m) => (
              <span key={m.user_id} className="av" style={{ background: m.color }} />
            ))}
            {Array.from({ length: Math.max(0, 4 - familyMembers.length) }).map((_, i) => (
              <span key={`empty-${i}`} className="av av--empty">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M12 5v14M5 12h14" strokeLinecap="round" />
                </svg>
              </span>
            ))}
          </div>
          <span className="lbl tnum">{t('fam.comp.members', { n: familyMembers.length })}</span>
          <span className="sep" aria-hidden="true" />
          <span className="shared">
            <span className="dot" />
            {t('fam.comp.shared')}
          </span>
        </div>

        <div
          className="seg"
          role="tablist"
          style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap' }}
        >
          {(
            [
              ['members', t('fam.tab.members')],
              ['therapy', t('fam.tab.therapy')],
              ['settings', t('fam.tab.settings')],
            ] as [TopTab, string][]
          ).map(([k, lbl]) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={topTab === k}
              className={topTab === k ? 'on' : ''}
              onClick={() => setTopTab(k)}
              data-family-top-tab={k}
            >
              {lbl}
            </button>
          ))}
        </div>

        {familyFlash && (
          <output className="card fam-flash" style={{ marginBottom: 16 }}>
            <span className="dot" aria-hidden="true" />
            <span className="fam-flash-msg">{familyFlash}</span>
            <Link
              className="btn btn-sm"
              href="/family?tab=settings&subtab=key"
              style={{ marginLeft: 'auto' }}
            >
              {L2({ en: 'Add family key →', ru: 'Добавить семейный ключ →' })}
            </Link>
          </output>
        )}

        {topTab === 'members' && (
          <>
            <MembersCard familyMembers={familyMembers} principal={principal} family={family} />
            <TherapyCTACard familyProvider={familyProvider} onOpen={onOpenFamilyChat} />
          </>
        )}

        {topTab === 'therapy' && (
          <TherapyCTACard familyProvider={familyProvider} onOpen={onOpenFamilyChat} />
        )}

        {topTab === 'settings' && <FamilySettingsTabs />}
      </div>

      <p className="disc">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v5M12 16h.01" />
        </svg>
        <strong>{t('fam.disc')}</strong>
      </p>
    </>
  );
}

// ---- Sub-components used by the tab strip ----

function MembersCard({
  familyMembers,
  principal,
  family,
}: {
  familyMembers: FamilyMemberRecord[];
  principal: { user_id: string };
  family: { owner_user_id: string } | null;
}) {
  const { t } = useLang();
  return (
    <div className="fam-members">
      {/* Memory-layers diagram — a conceptual, honest illustration of the
          shared/private split (no fabricated member names; generic labels).
          The architecture is real: each member has a private layer the owner
          cannot read, plus a shared family layer. */}
      <div className="blk-head">
        <h2>{t('fam.layer.title')}</h2>
        <span className="eyebrow">{t('fam.layer.eyebrow')}</span>
      </div>
      <div className="card">
        <div className="layers">
          <svg
            className="layers__svg"
            viewBox="0 0 460 230"
            role="img"
            aria-label={t('fam.layer.title')}
          >
            <defs>
              <radialGradient id="famPrivGrad" cx="50%" cy="50%" r="50%">
                <stop
                  offset="0%"
                  stopColor="color-mix(in srgb, var(--label) 10%, var(--surface-2))"
                />
                <stop
                  offset="100%"
                  stopColor="color-mix(in srgb, var(--label) 5%, var(--surface-2))"
                />
              </radialGradient>
              <radialGradient id="famSharedGrad" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="var(--purple-soft)" />
                <stop offset="100%" stopColor="var(--purple-tint)" />
              </radialGradient>
            </defs>
            <circle
              cx="135"
              cy="115"
              r="92"
              fill="url(#famPrivGrad)"
              stroke="var(--border)"
              strokeWidth="1"
            />
            <text
              x="135"
              y="105"
              textAnchor="middle"
              fontFamily="var(--mono)"
              fontSize="11"
              fill="var(--label)"
            >
              {t('fam.layer.private')}
            </text>
            <circle
              cx="325"
              cy="115"
              r="92"
              fill="url(#famPrivGrad)"
              stroke="var(--border)"
              strokeWidth="1"
            />
            <text
              x="325"
              y="105"
              textAnchor="middle"
              fontFamily="var(--mono)"
              fontSize="11"
              fill="var(--label)"
            >
              {t('fam.layer.private')}
            </text>
            <ellipse
              cx="230"
              cy="115"
              rx="58"
              ry="58"
              fill="url(#famSharedGrad)"
              stroke="var(--purple)"
              strokeWidth="1"
            />
            <text
              x="230"
              y="108"
              textAnchor="middle"
              fontFamily="var(--mono)"
              fontSize="11"
              fill="var(--purple)"
            >
              {t('fam.layer.shared')}
            </text>
            <text
              x="230"
              y="126"
              textAnchor="middle"
              fontFamily="var(--font)"
              fontSize="11"
              fill="var(--purple)"
            >
              {t('fam.layer.family')}
            </text>
          </svg>
          <div className="layers__legend">
            <span className="lg">
              <span className="sw sw--shared" />
              {t('fam.layer.legend.shared')}
            </span>
            <span className="lg">
              <span className="sw sw--priv" />
              {t('fam.layer.legend.priv')}
            </span>
          </div>
        </div>
      </div>

      <div className="blk-head">
        <h2>{t('fam.tab.members')}</h2>
        <span className="eyebrow tnum">{t('fam.comp.members', { n: familyMembers.length })}</span>
      </div>
      <div className="mems">
        {familyMembers.map((m: FamilyMemberRecord) => {
          const isMemOwner = family && m.user_id === family.owner_user_id;
          const isSelf = m.user_id === principal.user_id;
          return (
            <div key={m.user_id} className="mem">
              <span className="av" style={{ background: m.color }} aria-hidden="true">
                {(m.family_display_name || '?').charAt(0).toUpperCase()}
              </span>
              <div>
                <div className="mem__name">
                  {m.family_display_name}
                  {isMemOwner && <span className="you-tag">{t('fam.mem.role.owner')}</span>}
                  {isSelf && <span className="you-tag">{t('fam.mem.you')}</span>}
                </div>
                <div className="mem__role">
                  {isMemOwner ? t('fam.mem.role.owner') : t('fam.mem.role.member')}
                  {m.relation ? ` · ${m.relation}` : ''}
                </div>
              </div>
              <div className="mem__access">
                <span className="chip">{t('fam.mem.access')}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TherapyCTACard({
  familyProvider,
  onOpen,
}: {
  familyProvider: { key_handle?: string | null } | null;
  onOpen: () => void;
}) {
  const { t, L2 } = useLang();
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-title">{t('fam.therapy.title')}</div>
      <div className="help" style={{ marginBottom: 12 }}>
        {t('fam.therapy.sub')}
      </div>
      <div className="fam-actions">
        <button type="button" className="btn btn-primary" onClick={onOpen} data-family-therapy-cta>
          {t('fam.therapy.open')}
        </button>
      </div>
      {!familyProvider?.key_handle && (
        <div className="help" style={{ marginTop: 8, color: 'var(--label)' }}>
          {L2({
            en: 'No family key yet. Add one in Family settings → Family key.',
            ru: 'Семейного ключа ещё нет. Добавьте его в Настройках → Семейный ключ.',
          })}
        </div>
      )}
    </div>
  );
}
