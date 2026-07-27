'use client';

// Persona picker shown when the user hits "New chat" (Rail button or the
// conversations drawer). Instead of silently starting a chat with the active
// persona, the user chooses who to start with — a grid of the builtin + custom
// companions. Mounted once in AppShell so it overlays any screen.

import { PersonaCard } from '@/components/screens/PersonaCard';
import { useLang } from '@/lib/i18n';
import { useStore } from '@/lib/store';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

export function NewChatPicker() {
  const { t, L2 } = useLang();
  const router = useRouter();
  const open = useStore((s) => s.newChatPickerOpen);
  const close = useStore((s) => s.closeNewChatPicker);
  const personas = useStore((s) => s.personas);
  const list = personas();
  const activePersona = useStore((s) => s.activePersonaId);
  const startChatWith = useStore((s) => s.startChatWith);

  // Esc closes. Avoid the zustand v5 useSyncExternalStore trap: select the
  // stable function refs, not a freshly-computed array (see MemoryScreen #185).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, close]);

  if (!open) return null;

  const pick = (personaId: string) => {
    startChatWith(personaId);
    close();
    router.push('/chat');
  };

  // The 'fam' persona is a family-psychologist surface that's intentionally
  // surfaced from /family (not the personal chat picker) — it requires a
  // family vault + a family LLM key, neither of which a fresh user has. We
  // filter it out here so the picker only ever shows companions that work
  // for a standalone personal chat. The /family Members tab has its own
  // "Open family therapy" CTA that sets ``activePersonaId = 'fam'`` and
  // routes to /chat once the family side is actually set up.
  const personal = list.filter((p) => p.id !== 'fam');

  return (
    <div
      className="picker-overlay"
      role="presentation"
      onClick={(e) => {
        // Backdrop click only — clicks inside `.picker` don't reach here.
        if (e.target === e.currentTarget) close();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') close();
      }}
    >
      <div className="picker">
        <div className="picker-head">
          <div>
            <h3>{t('np.title')}</h3>
            <span className="picker-sub">{t('np.sub')}</span>
          </div>
          <button type="button" className="icon-btn" aria-label={t('np.close')} onClick={close}>
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.7}
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div className="persona-grid stagger">
          {personal.map((p, i) => (
            <PersonaCard
              key={p.id}
              persona={p}
              selected={p.id === activePersona}
              index={i}
              onSelect={() => pick(p.id)}
              onChat={() => pick(p.id)}
            />
          ))}
        </div>
        {/* Phase 3 #17: a "Family therapy" shortcut below the personal grid.
            It links to /family?tab=therapy (NOT startChatWith('fam')) so the
            /family setup gate still applies — the family vault + key must
            exist before a family turn can run. ``fam`` stays filtered out of
            the personal grid above (it's not a standalone personal chat). */}
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 12, paddingTop: 12 }}>
          <div className="alt-line" style={{ color: 'var(--muted, #8a8a98)', marginBottom: 6 }}>
            {t('np.family.sub')}
          </div>
          <Link
            href="/family?tab=therapy"
            className="btn btn-primary"
            data-family-therapy-pick
            onClick={() => close()}
            style={{ width: '100%', textAlign: 'center' }}
          >
            {t('np.family.open')}
          </Link>
        </div>
      </div>
    </div>
  );
}
