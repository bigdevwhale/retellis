'use client';

// Telegram integration card — lives in Settings → Integrations.
//
// Two states:
//  - no bot yet: an init form (paste a @BotFather token, pick a persona). On
//    submit the server validates the token via getMe and returns a connect
//    link; we show step 2 ("open your bot in Telegram, send /start <token>",
//    then approve in Stillside").
//  - bot exists: a status card (status badge, persona, @username, chat id,
//    last error) with Pause/Resume, persona switch, and Disconnect.
//
// Honest-limits copy (disclose, don't perform): the bot token and BYOK keys
// are both server-side envelope-encrypted under MESSENGER_TOKEN_DEK — the
// server CAN decrypt them at reply time. This is NOT zero-knowledge. The UI
// states only the neutral fact ("encrypted in transit and at rest on the
// server") and points to SECURITY.md for the full disclosure; it never claims
// zero-knowledge / on-device / "only you can read it".

import {
  type TelegramInitResponse,
  deleteMessenger,
  initTelegramBot,
  patchMessenger,
} from '@/lib/api-client';
import { useStore } from '@/lib/store';
import type { Messenger } from '@ai-companion/contracts';
import { useState } from 'react';

type L2 = (o: { en: string; ru: string }) => string;

type Props = {
  messengers: Messenger[];
  onChanged: () => void; // refetch list after a mutation
  L2: L2;
};

const STATUS_LABEL: Record<Messenger['status'], { en: string; ru: string }> = {
  pending_handshake: { en: 'Awaiting Telegram link', ru: 'Ожидает привязки' },
  active: { en: 'Active', ru: 'Активен' },
  paused: { en: 'Paused', ru: 'Приостановлен' },
  error: { en: 'Error', ru: 'Ошибка' },
};

const STATUS_DOT: Record<Messenger['status'], string> = {
  pending_handshake: 'var(--warn, #d4a23a)',
  active: 'var(--mint)',
  paused: 'var(--muted, #888)',
  error: 'var(--danger, #d23a3a)',
};

export function TelegramIntegration({ messengers, onChanged, L2 }: Props) {
  // Select the personas *function* (a stable reference), not the result of
  // calling it — `s.personas()` returns a fresh array each call, which makes
  // useSyncExternalStore see a new snapshot every render → infinite loop
  // (React error #185). Every other screen selects `s.personas` and calls it.
  const personasFn = useStore((s) => s.personas);
  const personas = personasFn();
  const [token, setToken] = useState('');
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? 'aria');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [init, setInit] = useState<TelegramInitResponse | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const bot = messengers.find((m) => m.kind === 'telegram') ?? null;

  async function submitInit() {
    setBusy(true);
    setError(null);
    try {
      const res = await initTelegramBot({ bot_token: token.trim(), persona_id: personaId });
      setInit(res);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function setPersona(id: string, pid: string) {
    try {
      await patchMessenger(id, { persona_id: pid });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function setStatus(id: string, status: 'active' | 'paused') {
    setBusy(true);
    setError(null);
    try {
      await patchMessenger(id, { status });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    setError(null);
    try {
      await deleteMessenger(id);
      setConfirmingDelete(null);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // --- Already-connected bot: status card + controls ---
  if (bot) {
    return (
      <div className="tg-integration">
        <div className="set-stat" style={{ marginBottom: 8 }}>
          <span className="dot" style={{ background: STATUS_DOT[bot.status] }} aria-hidden="true" />
          <b>@{bot.bot_username ?? 'bot'}</b>
          <span className="tag">{L2(STATUS_LABEL[bot.status])}</span>
          <span className="chip" title={L2({ en: 'Bot token (masked)', ru: 'Токен бота (маска)' })}>
            {bot.bot_token_masked}
          </span>
        </div>

        {bot.last_error && (
          <div className="alt-line" style={{ color: 'var(--danger, #d23a3a)', marginBottom: 8 }}>
            {bot.last_error}
          </div>
        )}

        <div className="grid grid-2" style={{ gap: 12 }}>
          <label className="field">
            <span className="help">{L2({ en: 'Persona', ru: 'Персона' })}</span>
            <select
              value={bot.persona_id}
              onChange={(e) => setPersona(bot.id, e.target.value)}
              disabled={busy}
            >
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            <span className="help">{L2({ en: 'Chat id', ru: 'Chat id' })}</span>
            <span className="tnum">{bot.chat_id ?? '—'}</span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          {bot.status === 'active' ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setStatus(bot.id, 'paused')}
              disabled={busy}
            >
              {L2({ en: 'Pause', ru: 'Приостановить' })}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => setStatus(bot.id, 'active')}
              disabled={busy}
            >
              {L2({ en: 'Resume', ru: 'Возобновить' })}
            </button>
          )}
          <button
            type="button"
            className="btn btn-sm btn-danger-ghost"
            onClick={() => setConfirmingDelete(bot.id)}
            disabled={busy}
          >
            {L2({ en: 'Disconnect', ru: 'Отключить' })}
          </button>
        </div>

        {confirmingDelete && (
          <div className="set-confirm" style={{ marginTop: 8 }}>
            <span className="help">
              {L2({
                en: 'Disconnect this bot? The token + bound BYOK key are erased.',
                ru: 'Отключить бота? Токен и привязанный BYOK-ключ будут стёрты.',
              })}
            </span>
            <button
              type="button"
              className="btn btn-sm btn-danger-ghost"
              onClick={() => remove(confirmingDelete)}
              disabled={busy}
            >
              {L2({ en: 'Yes, disconnect', ru: 'Да, отключить' })}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => setConfirmingDelete(null)}
            >
              {L2({ en: 'Cancel', ru: 'Отмена' })}
            </button>
          </div>
        )}

        <p className="help" style={{ marginTop: 8 }}>
          {L2({
            en: 'The bot token and your API keys are encrypted in transit and at rest on the server. See SECURITY.md for how keys are stored.',
            ru: 'Токен бота и ваши API-ключи шифруются при передаче и хранятся зашифрованными на сервере. Подробности о хранении ключей — в SECURITY.md.',
          })}
        </p>

        {error && (
          <div className="alt-line" style={{ marginTop: 8, color: 'var(--warn, #d4a23a)' }}>
            {error}
          </div>
        )}
      </div>
    );
  }

  // --- Step 2: init succeeded, show the connect link + instructions ---
  if (init) {
    const url = init.connect_url;
    return (
      <div className="tg-integration">
        <p className="card-desc">
          {L2({
            en: 'Bot validated. Now open your bot in Telegram and send:',
            ru: 'Бот проверен. Откройте бота в Telegram и отправьте:',
          })}
        </p>
        <code className="alt-line" style={{ display: 'block', padding: 8, wordBreak: 'break-all' }}>
          /start {init.connect_token}
        </code>
        <p className="help" style={{ marginTop: 8 }}>
          {L2({
            en: 'The bot replies with a Connect button — it opens this browser to approve binding your BYOK key. No BYOK? The bot uses the server fallback chain.',
            ru: 'Бот ответит кнопкой Connect — она откроет этот браузер для подтверждения привязки BYOK-ключа. Нет BYOK? Бот будет использовать серверный fallback.',
          })}
        </p>
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <a className="btn btn-sm btn-primary" href={url}>
            {L2({ en: 'Open connect link', ru: 'Открыть ссылку привязки' })}
          </a>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setInit(null)}>
            {L2({ en: 'Back', ru: 'Назад' })}
          </button>
        </div>
        {error && (
          <div className="alt-line" style={{ marginTop: 8, color: 'var(--warn, #d4a23a)' }}>
            {error}
          </div>
        )}
      </div>
    );
  }

  // --- Step 1: init form ---
  return (
    <div className="tg-integration">
      <p className="card-desc">
        {L2({
          en: 'Talk to your companion from Telegram — same memory, same persona as the web app.',
          ru: 'Общайтесь с компаньоном из Telegram — та же память и персона, что и в вебе.',
        })}
      </p>
      <div className="grid grid-2" style={{ gap: 12 }}>
        <label className="field">
          <span className="help">
            {L2({ en: 'Bot token (from @BotFather)', ru: 'Токен бота (из @BotFather)' })}
          </span>
          <input
            type="text"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="123456789:ABCdef..."
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <label className="field">
          <span className="help">{L2({ en: 'Persona', ru: 'Персона' })}</span>
          <select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={submitInit}
          disabled={busy || token.trim().length < 10}
        >
          {busy
            ? L2({ en: 'Validating…', ru: 'Проверка…' })
            : L2({ en: 'Connect Telegram', ru: 'Подключить Telegram' })}
        </button>
      </div>
      {error && (
        <div className="alt-line" style={{ marginTop: 8, color: 'var(--warn, #d4a23a)' }}>
          {error}
        </div>
      )}
    </div>
  );
}

export default TelegramIntegration;
