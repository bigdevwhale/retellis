'use client';

// Soft email-verification banner. Shown to a signed-in user whose account is
// not yet email_verified AND whose deployment has FEATURE_EMAIL_VERIFICATION
// on. Soft — no backend endpoint is gated; this is a reminder + a Resend
// affordance. Mirrors the LoginScreen post-signup panel but lives in the app
// shell so it follows the user into the app until they verify.
//
// Also surfaces a one-shot toast when the verify-email redirect lands with
// ?verify=failed (invalid/expired token), so the email click that didn't
// verify isn't silently ignored.

import { resendVerificationEmail } from '@/lib/api-client';
import { useLang } from '@/lib/i18n';
import { toast } from '@/lib/toast';
import { useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { useAuthCtx } from '@/lib/auth';

export function EmailVerifyBanner() {
  const { principal, config } = useAuthCtx();
  const { L2, lang } = useLang();
  const search = useSearchParams();
  const verifyFailed = search.get('verify') === 'failed';

  const [dismissed, setDismissed] = useState(false);
  const [resendBusy, setResendBusy] = useState(false);
  const [resendDone, setResendDone] = useState(false);

  // One-shot toast on a failed verify redirect. The ref guard means a language
  // toggle (which changes L2 and re-runs the effect) does NOT re-toast — the
  // failed-verify notice fires once per mount, not once per lang change.
  const failedToastFired = useRef(false);
  useEffect(() => {
    if (failedToastFired.current || !verifyFailed) return;
    failedToastFired.current = true;
    toast.error(
      L2({
        en: 'Verification link is invalid or expired — request a new one.',
        ru: 'Ссылка подтверждения недействительна или истекла — запросите новую.',
      }),
      { duration: 8000 },
    );
  }, [verifyFailed, L2]);

  const show =
    !!principal && !principal.email_verified && !!config?.features.email_verification && !dismissed;

  if (!show) return null;

  const onResend = async (e: React.FormEvent) => {
    e.preventDefault();
    setResendBusy(true);
    setResendDone(false);
    try {
      await resendVerificationEmail(principal?.email ?? '', lang);
      setResendDone(true);
    } catch {
      toast.error(L2({ en: 'Could not resend the link.', ru: 'Не удалось отправить ссылку.' }));
    } finally {
      setResendBusy(false);
    }
  };

  return (
    // biome-ignore lint/a11y/useSemanticElements: role="status" is the correct ARIA polite live region; no equivalent HTML element fits a dismissable banner.
    <div className="verify-banner" role="status">
      <span className="verify-banner-msg">
        {resendDone
          ? L2({
              en: 'Sent another verification link — check your inbox.',
              ru: 'Отправили ещё одну ссылку — проверьте почту.',
            })
          : L2({
              en: 'Verify your email to confirm it’s yours.',
              ru: 'Подтвердите почту, чтобы мы знали, что она ваша.',
            })}
      </span>
      <button
        type="button"
        className="verify-banner-action"
        onClick={onResend}
        disabled={resendBusy}
      >
        {resendBusy
          ? L2({ en: 'Sending…', ru: 'Отправка…' })
          : L2({ en: 'Resend link', ru: 'Отправить снова' })}
      </button>
      <button
        type="button"
        className="verify-banner-close"
        aria-label={L2({ en: 'Dismiss', ru: 'Скрыть' })}
        onClick={() => setDismissed(true)}
      >
        <svg
          viewBox="0 0 24 24"
          width="14"
          height="14"
          aria-hidden="true"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </div>
  );
}
