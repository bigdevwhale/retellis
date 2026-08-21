'use client';

// Login / sign-up screen. Renders mode-appropriate options from the public
// /v1/config (which auth backends this deployment enables). The web never
// verifies tokens — it drives FastAPI-owned flows and lets the server set the
// session cookie. The BYOK API key is added separately on /onboarding after
// sign-in; it is never collected on this screen.

import type { AuthBackendKind, AuthConfig } from '@ai-companion/contracts';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import {
  getAuthConfig,
  localLogin,
  localSignup,
  magicLinkRequest,
  resendVerificationEmail,
} from '@/lib/api-client';
import { useLang } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

/** Brand mark — a crescent moon over still water (the "still side" of evening). */
function BrandGlyph() {
  return (
    <svg viewBox="0 0 48 48" fill="none" role="img" aria-label="Retellis">
      <title>Retellis</title>
      <defs>
        <linearGradient id="lg-still" x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
          <stop stopColor="#533afd" />
          <stop offset="1" stopColor="#9d8be0" />
        </linearGradient>
      </defs>
      <circle cx="24" cy="20" r="11" stroke="url(#lg-still)" strokeWidth="1.4" opacity="0.45" />
      <path d="M30.5 20a8 8 0 1 1-7.4-7.9 6.4 6.4 0 0 0 7.4 7.9Z" fill="url(#lg-still)" />
      <line x1="7" y1="34" x2="41" y2="34" stroke="url(#lg-still)" strokeWidth="1.4" />
      <path
        d="M14 38.5c2.2-2 4.4-2 6.6 0s4.4 2 6.6 0 4.4-2 6.6 0"
        stroke="url(#lg-still)"
        strokeWidth="1.2"
        opacity="0.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function LoginScreen() {
  const { L2, lang, toggleLang } = useLang();
  const { toggle: toggleTheme } = useTheme();
  const search = useSearchParams();
  const next = search.get('next');

  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [magicSent, setMagicSent] = useState(false);
  // Soft email verification: after a local signup with the feature on, the
  // session is already set but the user is unverified — show a "check your
  // email" panel (Resend + Continue) instead of hard-navigating away. The
  // user can continue into the app immediately (soft flow).
  const [verifySent, setVerifySent] = useState(false);
  const [verifyEmail, setVerifyEmail] = useState('');
  const [resendBusy, setResendBusy] = useState(false);
  const [resendDone, setResendDone] = useState(false);

  // Local-account form state.
  const [mode, setMode] = useState<'login' | 'signup'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');

  // Magic-link form state.
  const [magicEmail, setMagicEmail] = useState('');

  useEffect(() => {
    getAuthConfig()
      .then(setConfig)
      .catch((e) => setError(String(e)));
  }, []);

  const backends: AuthBackendKind[] = config?.auth_backends ?? [];
  const isHosted = config?.mode === 'hosted';

  const finish = () => {
    // Cookie is now set; let middleware/app shell take over. A hard navigation
    // avoids serving cached unauthenticated state. On hosted, a sign-in with no
    // explicit `next` lands in /chat (lazy onboarding — chat first, keys later);
    // self-hosted lands on / as before.
    const dest = next ?? (isHosted ? '/chat' : '/');
    window.location.href = dest;
  };

  const onLocal = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === 'signup') {
        await localSignup({ email, password, display_name: name || undefined, lang });
        // Soft verification: when the feature is on, the account is created
        // unverified and a link was emailed. The session is already set, so
        // we *could* finish() — but first surface a "check your email" panel
        // with a Resend + Continue. The user is not locked out.
        if (config?.features.email_verification) {
          setVerifyEmail(email);
          setVerifySent(true);
          setBusy(false);
          return;
        }
      } else {
        await localLogin({ email, password });
      }
      finish();
    } catch (err) {
      // The server returns a non-enumerating 401 "invalid email or password" for
      // login mismatches; surface a generic message either way.
      setError(
        mode === 'signup'
          ? L2({
              en: 'Could not create the account. Try a different email.',
              ru: 'Не удалось создать аккаунт. Попробуйте другую почту.',
            })
          : L2({ en: 'Incorrect email or password.', ru: 'Неверная почта или пароль.' }),
      );
    } finally {
      setBusy(false);
    }
  };

  const onMagic = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await magicLinkRequest(magicEmail);
      setMagicSent(true);
    } catch {
      setError(L2({ en: 'Could not send the sign-in link.', ru: 'Не удалось отправить ссылку.' }));
    } finally {
      setBusy(false);
    }
  };

  const onResend = async (e: React.FormEvent) => {
    e.preventDefault();
    setResendBusy(true);
    setResendDone(false);
    try {
      await resendVerificationEmail(verifyEmail, lang);
      setResendDone(true);
    } catch {
      // Non-enumerating endpoint; a failure here is config drift / network —
      // surface a generic message rather than claiming a resend happened.
      setError(L2({ en: 'Could not resend the link.', ru: 'Не удалось отправить ссылку снова.' }));
    } finally {
      setResendBusy(false);
    }
  };

  // Ordered list of enabled sections so we can drop dividers *between* them.
  const sections: AuthBackendKind[] = [];
  if (backends.includes('oidc')) sections.push('oidc');
  if (backends.includes('magic_link')) sections.push('magic_link');
  if (backends.includes('local')) sections.push('local');

  return (
    <div className="login-screen">
      <div className="login-aurora" aria-hidden />

      <div className="login-controls">
        <button
          type="button"
          className="icon-mini lang"
          title={lang === 'ru' ? 'Язык' : 'Language'}
          onClick={toggleLang}
        >
          <span className="lbl">{lang.toUpperCase()}</span>
        </button>
        <button
          type="button"
          className="icon-mini"
          title={L2({ en: 'Toggle theme', ru: 'Сменить тему' })}
          onClick={toggleTheme}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.6}
          >
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
          </svg>
        </button>
      </div>

      <div className="login-stage">
        <header className="login-brand">
          <div className="login-glyph">
            <BrandGlyph />
          </div>
          <h1>{L2({ en: 'Retellis', ru: 'Retellis' })}</h1>
          <p className="login-tagline">
            {L2({
              en: 'A companion that remembers what mattered.',
              ru: 'Собеседник, который помнит то, что было важно.',
            })}
          </p>
        </header>

        <div className="login-card">
          {!config ? (
            <div className="login-note" style={{ textAlign: 'center', margin: 0 }}>
              {L2({ en: 'Loading…', ru: 'Загрузка…' })}
            </div>
          ) : (
            <>
              {sections.map((kind, i) => (
                <div key={kind}>
                  {i > 0 && <div className="login-divider">{L2({ en: 'or', ru: 'или' })}</div>}

                  {kind === 'oidc' && (
                    <div className="login-section">
                      <a className="btn btn-primary" href="/v1/auth/begin">
                        {isHosted
                          ? L2({
                              en: 'Continue with Google / GitHub',
                              ru: 'Войти через Google / GitHub',
                            })
                          : L2({ en: 'Continue with single sign-on', ru: 'Войти через SSO' })}
                      </a>
                      <p className="login-note">
                        {L2({
                          en: 'You will be redirected to your identity provider.',
                          ru: 'Вы будете перенаправлены к провайдеру входа.',
                        })}
                      </p>
                    </div>
                  )}

                  {kind === 'magic_link' && (
                    <div className="login-section">
                      <p className="login-section-title">
                        {L2({ en: 'Sign in with email', ru: 'Вход по почте' })}
                      </p>
                      {magicSent ? (
                        <p className="login-note">
                          {L2({
                            en: `Check ${magicEmail} for a sign-in link.`,
                            ru: `Проверьте ${magicEmail} — мы отправили ссылку для входа.`,
                          })}
                        </p>
                      ) : (
                        <form onSubmit={onMagic}>
                          <div className="field">
                            <label htmlFor="magic-email">Email</label>
                            <input
                              id="magic-email"
                              className="input"
                              type="email"
                              required
                              value={magicEmail}
                              onChange={(e) => setMagicEmail(e.target.value)}
                              autoComplete="email"
                              inputMode="email"
                              enterKeyHint="send"
                              disabled={busy}
                            />
                          </div>
                          <button type="submit" className="btn btn-primary" disabled={busy}>
                            {L2({ en: 'Send sign-in link', ru: 'Отправить ссылку' })}
                          </button>
                        </form>
                      )}
                    </div>
                  )}

                  {kind === 'local' && (
                    <div className="login-section">
                      <p className="login-section-title">
                        {mode === 'signup'
                          ? L2({ en: 'Create a local account', ru: 'Создать локальный аккаунт' })
                          : L2({
                              en: 'Sign in with a local account',
                              ru: 'Войти через локальный аккаунт',
                            })}
                      </p>
                      {verifySent ? (
                        <div className="login-verify-panel">
                          <p className="login-note">
                            {L2({
                              en: `We sent a verification link to ${verifyEmail}. Click it to confirm your email.`,
                              ru: `Мы отправили ссылку для подтверждения на ${verifyEmail}. Перейдите по ней, чтобы подтвердить почту.`,
                            })}
                          </p>
                          {resendDone && (
                            // biome-ignore lint/a11y/useSemanticElements: role="status" announces the resend result to AT as a polite live region.
                            <p className="login-note" role="status">
                              {L2({
                                en: 'Sent another link. Check your inbox.',
                                ru: 'Отправили ещё одну ссылку. Проверьте почту.',
                              })}
                            </p>
                          )}
                          {error && (
                            <div className="login-error" role="alert">
                              {error}
                            </div>
                          )}
                          <button
                            type="button"
                            className="btn btn-secondary"
                            onClick={onResend}
                            disabled={resendBusy}
                          >
                            {resendBusy
                              ? L2({ en: 'Sending…', ru: 'Отправка…' })
                              : L2({ en: 'Resend link', ru: 'Отправить снова' })}
                          </button>
                          <button type="button" className="btn btn-primary" onClick={finish}>
                            {L2({ en: 'Continue →', ru: 'Продолжить →' })}
                          </button>
                          <p className="login-note">
                            {L2({
                              en: 'You can use Retellis now — verifying just confirms the email is yours.',
                              ru: 'Вы можете пользоваться Retellis уже сейчас — подтверждение лишь проверяет, что почта ваша.',
                            })}
                          </p>
                        </div>
                      ) : (
                        <>
                          <form onSubmit={onLocal}>
                            {mode === 'signup' && (
                              <div className="field">
                                <label htmlFor="lo-name">
                                  {L2({ en: 'Display name', ru: 'Отображаемое имя' })}
                                </label>
                                <input
                                  id="lo-name"
                                  className="input"
                                  value={name}
                                  onChange={(e) => setName(e.target.value)}
                                  autoComplete="name"
                                  enterKeyHint="next"
                                  disabled={busy}
                                />
                              </div>
                            )}
                            <div className="field">
                              <label htmlFor="lo-email">Email</label>
                              <input
                                id="lo-email"
                                className="input"
                                type="email"
                                required
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                autoComplete="email"
                                inputMode="email"
                                enterKeyHint="next"
                                disabled={busy}
                              />
                            </div>
                            <div className="field">
                              <label htmlFor="lo-pw">{L2({ en: 'Password', ru: 'Пароль' })}</label>
                              <input
                                id="lo-pw"
                                className="input"
                                type="password"
                                required
                                minLength={8}
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                autoComplete={
                                  mode === 'signup' ? 'new-password' : 'current-password'
                                }
                                autoCapitalize="off"
                                autoCorrect="off"
                                spellCheck={false}
                                enterKeyHint="go"
                                disabled={busy}
                              />
                            </div>
                            {error && (
                              <div className="login-error" role="alert">
                                {error}
                              </div>
                            )}
                            <button type="submit" className="btn btn-primary" disabled={busy}>
                              {mode === 'signup'
                                ? L2({ en: 'Create account', ru: 'Создать аккаунт' })
                                : L2({ en: 'Sign in', ru: 'Войти' })}
                            </button>
                          </form>
                          <button
                            type="button"
                            className="login-toggle"
                            onClick={() => {
                              setMode((m) => (m === 'login' ? 'signup' : 'login'));
                              setError(null);
                            }}
                          >
                            {mode === 'login'
                              ? L2({ en: 'No account? Sign up', ru: 'Нет аккаунта? Создать' })
                              : L2({
                                  en: 'Already have an account? Sign in',
                                  ru: 'Уже есть аккаунт? Войти',
                                })}
                          </button>
                          <p className="login-note">
                            {L2({
                              en: 'Your login password is separate from your BYOK API key, which is added after sign-in.',
                              ru: 'Пароль входа отдельно от вашего ключа API BYOK, который добавляется после входа.',
                            })}
                          </p>
                        </>
                      )}
                    </div>
                  )}
                </div>
              ))}

              {sections.length === 0 && (
                <p className="login-note" style={{ textAlign: 'center', margin: 0 }}>
                  {L2({
                    en: 'Sign-in is not configured on this instance. Contact the operator.',
                    ru: 'Вход на этом узле не настроен. Обратитесь к администратору.',
                  })}
                </p>
              )}
            </>
          )}
        </div>

        <footer className="login-foot">
          <Link href="/">{L2({ en: 'Home', ru: 'На главную' })}</Link>
          <span className="brand-foot">
            {isHosted
              ? L2({ en: 'Sign in to your account', ru: 'Войдите в аккаунт' })
              : L2({ en: 'Sign in to this instance', ru: 'Войдите на этот узел' })}
          </span>
        </footer>
      </div>
    </div>
  );
}
