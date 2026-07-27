'use client';

// Settings → Integrations section. Loads the user's messenger bots and renders
// one card per adapter kind (Telegram today; the shape generalizes — a future
// WhatsApp/Signal adapter slots in here without touching the loader).

import { listMessengers } from '@/lib/api-client';
import { useLang } from '@/lib/i18n';
import type { Messenger } from '@ai-companion/contracts';
import { useCallback, useEffect, useState } from 'react';
import { TelegramIntegration } from './TelegramIntegration';

export function IntegrationsSection() {
  const { L2 } = useLang();
  const [messengers, setMessengers] = useState<Messenger[]>([]);
  const [loaded, setLoaded] = useState(false);

  const reload = useCallback(async () => {
    try {
      setMessengers(await listMessengers());
    } catch {
      // Network/down — leave the previous list (or empty). The card's own
      // error surface handles per-action failures; a load failure just shows
      // the empty init form so the user can still try to connect.
      setMessengers([]);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <section className="card" aria-labelledby="integrations-h">
      <h3 className="card-title" id="integrations-h">
        {L2({ en: 'Integrations', ru: 'Интеграции' })}
      </h3>
      <p className="card-desc">
        {L2({
          en: 'Connect a messenger so you can talk to your companion from there. Messages share the same memory and persona as the web app.',
          ru: 'Подключите мессенджер, чтобы общаться с компаньоном оттуда. Сообщения используют ту же память и персону, что и веб-приложение.',
        })}
      </p>

      {!loaded ? (
        <div className="help" style={{ margin: 0 }}>
          {L2({ en: 'Loading…', ru: 'Загрузка…' })}
        </div>
      ) : (
        <TelegramIntegration messengers={messengers} onChanged={reload} L2={L2} />
      )}
    </section>
  );
}

export default IntegrationsSection;
