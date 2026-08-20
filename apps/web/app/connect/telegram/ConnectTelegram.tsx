'use client';

// Telegram deep-link handshake page.
//
// Reached from the bot's inline Connect button (`/connect/telegram?messenger=<id>
// &token=<connect_token>`). The user is already logged in to the web (AuthGate
// sends them through /login?next=… otherwise). This page shows what binding
// means and asks for explicit approval.
//
// The bot uses the SAME server-side BYOK key the web chat uses — the server
// resolves the user's envelope-encrypted provider key at reply time, so this
// page no longer seals anything. We just bind the bot to the persona with
// `byok_enc_key_blob: null`; the server-side envelope store handles the key
// for both the web and Telegram turns. If the user has no provider row yet,
// we honestly say so: the bot will use the server-fallback chain
// (env key, or the offline mock stand-in if none is set).
//
// On success the bot is active and the poller is started server-side; we send
// the user to Settings → Integrations to confirm.

import {
  bindTelegramBot,
  isTransientOrNetworkError,
  listMessengers,
  listProviders,
} from '@/lib/api-client';
import { useLang } from '@/lib/i18n';
import type { Messenger } from '@ai-companion/contracts';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

export function ConnectTelegram() {
  const { L2 } = useLang();
  const router = useRouter();
  const params = useSearchParams();
  const messengerId = params.get('messenger');
  const connectToken = params.get('token');

  const [bot, setBot] = useState<Messenger | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  // Whether the user has at least one server-side provider row. The bot
  // uses the same server-resolved BYOK key as the web chat, so a provider
  // row means "real key"; no row means "server fallback (env / mock)".
  const [hasProvider, setHasProvider] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!messengerId) {
        setError(L2({ en: 'Missing messenger id.', ru: 'Отсутствует id бота.' }));
        setLoading(false);
        return;
      }
      try {
        const [list, providers] = await Promise.all([listMessengers(), listProviders()]);
        if (cancelled) return;
        setBot(list.find((m) => m.id === messengerId) ?? null);
        setHasProvider(providers.length > 0);
      } catch {
        if (!cancelled)
          setError(L2({ en: 'Could not reach the server.', ru: 'Сервер недоступен.' }));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [messengerId, L2]);

  async function approve() {
    if (!messengerId || !connectToken) return;
    setBusy(true);
    setError(null);
    try {
      // The bot uses the same server-side BYOK key the web chat uses —
      // the server resolves it from its envelope store at reply time.
      // No key is sealed from this page; byok_enc_key_blob is always null.
      await bindTelegramBot(messengerId, connectToken, { byok_enc_key_blob: null });
      setDone(true);
      // Give the user a beat to read "connected", then land on the integrations tab.
      setTimeout(() => router.push('/settings?tab=integrations'), 1200);
    } catch (e) {
      if (e instanceof Error && e.message.includes('400')) {
        setError(
          L2({
            en: 'This connect link is invalid or expired. Start over in Settings → Integrations.',
            ru: 'Ссылка привязки недействительна или истекла. Начните заново в Настройки → Интеграции.',
          }),
        );
      } else if (isTransientOrNetworkError(e)) {
        setError(
          L2({
            en: 'Could not reach the server. Try again.',
            ru: 'Сервер недоступен. Попробуйте снова.',
          }),
        );
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wrap" style={{ maxWidth: 560 }}>
      <div className="topbar">
        <h2>{L2({ en: 'Connect Telegram bot', ru: 'Подключение Telegram-бота' })}</h2>
      </div>

      <section className="card">
        {loading ? (
          <p className="help">{L2({ en: 'Loading…', ru: 'Загрузка…' })}</p>
        ) : done ? (
          <>
            <div className="set-banner set-banner--ok">
              <span className="set-banner-line">
                <span className="dot" style={{ background: 'var(--mint)' }} aria-hidden="true" />
                {L2({
                  en: 'Connected. Your bot is active — send it a message in Telegram.',
                  ru: 'Подключено. Бот активен — отправьте ему сообщение в Telegram.',
                })}
              </span>
            </div>
          </>
        ) : !bot ? (
          <p className="card-desc">
            {error ??
              L2({
                en: 'No pending bot found for this link. Open Settings → Integrations to start over.',
                ru: 'Бот для этой ссылки не найден. Откройте Настройки → Интеграции, чтобы начать заново.',
              })}
          </p>
        ) : (
          <>
            <div className="set-stat" style={{ marginBottom: 8 }}>
              <b>@{bot.bot_username ?? 'bot'}</b>
              <span className="chip">{bot.bot_token_masked}</span>
              <span className="tag">
                {L2({ en: 'Persona', ru: 'Персона' })}: {bot.persona_id}
              </span>
            </div>
            <p className="card-desc">
              {L2({
                en: 'Approving connects this Telegram bot to your Retellis account. It will use the persona above and share the same memory as the web app.',
                ru: 'Подтверждение привязывает этого Telegram-бота к вашему аккаунту Retellis. Он будет использовать выбранную персону и ту же память, что и веб-приложение.',
              })}
            </p>

            <div className="alt-line" style={{ margin: '12px 0' }}>
              <span className="help">
                {hasProvider
                  ? L2({
                      en: 'Your BYOK key is stored envelope-encrypted on the server. The bot uses the same server-resolved key as the web chat — the server can decrypt it at reply time (NOT zero-knowledge; protects against a database dump, not the server operator).',
                      ru: 'Ваш BYOK-ключ хранится на сервере в конвертном шифровании. Бот использует тот же серверный ключ, что и веб-чат — сервер может расшифровать его при ответе (НЕ нулевое разглашение; защищает от дампа БД, а не от оператора сервера).',
                    })
                  : L2({
                      en: 'No provider key connected — the bot will use the server fallback (env key, or the offline stand-in if none is set). Connect a key in Settings to use your own.',
                      ru: 'Ключ провайдера не подключён — бот будет использовать серверный fallback (env-ключ или офлайн-заглушку, если ключа нет). Подключите ключ в Настройках, чтобы использовать свой.',
                    })}
              </span>
            </div>

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void approve()}
                disabled={busy}
              >
                {L2({ en: 'Approve', ru: 'Подтвердить' })}
              </button>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => router.push('/settings?tab=integrations')}
                disabled={busy}
              >
                {L2({ en: 'Cancel', ru: 'Отмена' })}
              </button>
            </div>
          </>
        )}

        {error && !loading && !done && bot && (
          <div className="alt-line" style={{ marginTop: 12, color: 'var(--warn, #d4a23a)' }}>
            {error}
          </div>
        )}
      </section>
    </div>
  );
}

export default ConnectTelegram;
