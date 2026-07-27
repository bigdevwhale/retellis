'use client';

// Informational showcase rendered to a signed-out visitor on an OD feature
// route. The sample content is taken verbatim from the Open Design project's
// own `.html` pages (chat/memory/journal/routing/family) — i.e. the designer-
// authored demo data those pages already ship — so the showcase is a faithful,
// honest preview of what each screen looks like, not invented copy. It is
// clearly labelled "sample" and never presented as the visitor's own data;
// nothing here makes an API call or touches a key.
//
// Security note: the OD chat page shows a fabricated key fingerprint
// (`sk-••••3a2f`) in its key indicator. We do NOT reproduce that here — the
// opaque key_handle is not the key, and fabricating a fingerprint on the
// client is a forbidden affective claim. The showcase shows an honest
// "BYOK · key set" indicator with no fingerprint.
//
// /practices is intentionally NOT handled: the practices screen is fully
// client-side and works as-is for a guest, so its route renders the real screen.

import { PERSONAS } from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';
import Link from 'next/link';

export type FeatureKey = 'chat' | 'memory' | 'journal' | 'routing' | 'persona' | 'family';

const TITLE: Record<FeatureKey, { en: string; ru: string }> = {
  chat: { en: 'Chat', ru: 'Чат' },
  memory: { en: 'Memory', ru: 'Память' },
  journal: { en: 'Journal', ru: 'Дневник' },
  routing: { en: 'Routing', ru: 'Маршрутизация' },
  persona: { en: 'Personas', ru: 'Персонажи' },
  family: { en: 'Family', ru: 'Семья' },
};

const LEDE_KEY: Record<FeatureKey, string> = {
  chat: 'guest.chat.lede',
  memory: 'guest.memory.lede',
  journal: 'guest.journal.lede',
  routing: 'guest.routing.lede',
  persona: 'guest.persona.lede',
  family: 'guest.family.lede',
};

export function GuestShowcase({ feature }: { feature: FeatureKey }) {
  const { t, L2 } = useLang();
  return (
    <main className="wrap guest-wrap">
      <div className="pagehead">
        <div className="pagehead__row">
          <div>
            <h1>{L2(TITLE[feature])}</h1>
            <p className="lede">{t(LEDE_KEY[feature])}</p>
          </div>
          <span className="guest-badge">
            <span className="dot" aria-hidden="true" />
            {t('guest.badge')}
          </span>
        </div>
      </div>

      {feature === 'chat' && <ChatSample />}
      {feature === 'memory' && <MemorySample />}
      {feature === 'journal' && <JournalSample />}
      {feature === 'routing' && <RoutingSample />}
      {feature === 'persona' && <PersonaSample />}
      {feature === 'family' && <FamilySample />}

      <div className="card guest-cta">
        <div className="card-title">{t('guest.cta.title')}</div>
        <div className="help" style={{ marginBottom: 14 }}>
          {t('guest.cta.note')}
        </div>
        <Link href="/login" className="btn btn-primary">
          {L2({ en: 'Sign in', ru: 'Войти' })}
        </Link>
      </div>
    </main>
  );
}

/* ---------- chat (OD chat.html) ---------- */

function ChatSample() {
  const { L2 } = useLang();
  // OD chat.html: Lina (therapist), the "call that won't let go" thread.
  const turns: { who: 'in' | 'out'; en: string; ru: string; t: string }[] = [
    {
      who: 'in',
      en: "Couldn't sleep again. That call was on my mind all day.",
      ru: 'Снова не спала. Думала о том звонке весь день.',
      t: '14:30',
    },
    {
      who: 'out',
      en: 'That call matters — tell me what stuck in it.',
      ru: 'Звонок важный — расскажи, что в нём застряло.',
      t: '14:31',
    },
    {
      who: 'in',
      en: 'Like I should have said more. I didn’t — and now it’s too late.',
      ru: 'Будто я должна была сказать больше. Не сказала — и теперь поздно.',
      t: '14:32',
    },
  ];
  return (
    <div className="card guest-sample guest-chat">
      <div className="gthread__head">
        <span className="gthread__av" aria-hidden="true" />
        <div>
          <div className="gthread__name">{L2({ en: 'Lina', ru: 'Лина' })}</div>
          <div className="gthread__role">{L2({ en: 'therapist', ru: 'терапевт' })}</div>
        </div>
        <span className="gthread__meta">
          {L2({ en: 'event-chain · 3 chains', ru: 'event-chain · 3 цепочки' })}
          <br />
          {L2({ en: 'persona: injected', ru: 'персаж: внедрён' })}
        </span>
      </div>
      <div className="gthread__canvas">
        <div className="memline">
          <span className="lbl">{L2({ en: 'Remembers:', ru: 'Помнит:' })}</span>
          <span className="chip chip--on">{L2({ en: 'the call', ru: 'разговор о звонке' })}</span>
          <span className="chip">{L2({ en: 'anxious · 0.72', ru: 'тревога · 0.72' })}</span>
          <span className="chip chip--dim">{L2({ en: 'insomnia', ru: 'бессонница' })}</span>
        </div>
        {turns.map((m, i) => (
          <div key={i} className={`msg msg--${m.who}`}>
            {L2({ en: m.en, ru: m.ru })}
            <div className="t">{m.t}</div>
          </div>
        ))}
        <div className="gfallback">
          {L2({
            en: 'Route changed: env → ollama. The turn continues.',
            ru: 'Маршрут сменился: env → ollama. Ход продолжается.',
          })}
        </div>
      </div>
      {/* Honest key indicator: NO fabricated fingerprint (the OD page shows
          sk-••••3a2f; we deliberately do not — fabricating a key fingerprint
          on the client is a forbidden affective claim). */}
      <div className="gkeyind">
        <span className="k">BYOK</span>
        <span className="v">{L2({ en: 'key set', ru: 'ключ установлен' })}</span>
      </div>
    </div>
  );
}

/* ---------- memory (OD memory.html, chains view) ---------- */

function MemorySample() {
  const { L2 } = useLang();
  // OD memory.html: statrow + the first chain ("The call that won't let go").
  const eline: {
    sal: string;
    hi?: boolean;
    dim?: boolean;
    low?: boolean;
    lbl: { en: string; ru: string };
  }[] = [
    { sal: '0.81', hi: true, lbl: { en: 'insomnia', ru: 'бессонница' } },
    { sal: '0.74', hi: true, lbl: { en: 'the call', ru: 'звонок' } },
    { sal: '0.52', dim: true, lbl: { en: 'guilt', ru: 'вина' } },
    { sal: '0.34', low: true, lbl: { en: 'acceptance', ru: 'смирение' } },
  ];
  const evs: { who: 'u' | 'l'; en: string; ru: string; t: string }[] = [
    {
      who: 'u',
      en: "Couldn't sleep again. That call was on my mind all day.",
      ru: 'Снова не спала. Думала о том звонке весь день.',
      t: '14:30',
    },
    {
      who: 'l',
      en: 'That call matters — tell me what stuck in it.',
      ru: 'Звонок важный — расскажи, что в нём застряло.',
      t: '14:31',
    },
    {
      who: 'u',
      en: 'Like I should have said more. I didn’t — and now it’s too late.',
      ru: 'Будто я должна была сказать больше. Не сказала — и теперь поздно.',
      t: '14:32',
    },
  ];
  return (
    <div className="guest-sample guest-memory">
      <div className="statrow tnum">
        <span className="n">184</span>&nbsp;{L2({ en: 'events', ru: 'события' })}
        <span className="sep">·</span>
        <span className="n">37</span>&nbsp;{L2({ en: 'memories', ru: 'памяти' })}
        <span className="sep">·</span>
        <span className="n">9</span>&nbsp;{L2({ en: 'chains', ru: 'цепочек' })}
      </div>
      <article className="gchain">
        <div className="gchain__head">
          <div className="gchain__title">
            {L2({ en: 'The call that won’t let go', ru: 'Звонок, который не отпускает' })}
          </div>
          <div className="gchain__meta tnum">
            {L2({ en: '4 events · salience 0.78', ru: '4 события · salience 0.78' })}
            <br />
            {L2({ en: '14:32 · Jul 16', ru: '14:32 · 16 июля' })}
          </div>
        </div>
        <div className="eline" role="img" aria-label="event line">
          {eline.map((n, i) => (
            <span key={i} className="eline__node">
              <span className={`eline__sal tnum${n.hi ? ' eline__sal--hi' : ''}`}>{n.sal}</span>
              <span
                className={`eline__dot${n.dim ? ' eline__dot--dim' : ''}${n.low ? ' eline__dot--low' : ''}`}
              />
              <span className="eline__lbl">{L2(n.lbl)}</span>
              {i < eline.length - 1 && <span className="eline__conn" aria-hidden="true" />}
            </span>
          ))}
        </div>
        <div className="gchain__events">
          {evs.map((e, i) => (
            <div key={i} className="gchain__ev">
              <span className={`who${e.who === 'u' ? ' who--u' : ''}`}>
                {e.who === 'u' ? 'user' : 'lina'}
              </span>
              <span className="txt">
                {L2({ en: e.en, ru: e.ru })}
                <span className="t">{e.t}</span>
              </span>
            </div>
          ))}
        </div>
        <div className="gchain__foot">
          <span className="salsum tnum">
            {L2({ en: 'avg salience', ru: 'средняя salience' })} 0.60
          </span>
        </div>
      </article>
    </div>
  );
}

/* ---------- journal (OD journal.html, entry feed) ---------- */

function JournalSample() {
  const { L2 } = useLang();
  // OD journal.html: real sample entries (mood/tags/matters authored by the
  // user, never generated — the author-note says so).
  const entries: {
    date: string;
    mood: { en: string; ru: string };
    moodVar: string;
    tags: { en: string; ru: string }[];
    matters: number;
    en: string;
    ru: string;
  }[] = [
    {
      date: '16 июл · 04:48',
      mood: { en: 'calm', ru: 'спокойствие' },
      moodVar: '--mood-calm',
      tags: [
        { en: 'sleep', ru: 'сон' },
        { en: 'autumn', ru: 'осень' },
      ],
      matters: 6,
      en: 'Third day in a row waking at 4:30. Not anxious — just the light comes early, and the room becomes different. I noticed the first half-hour of the day is now the quietest inside. Before the phone comes on.',
      ru: 'Третий день подряд просыпаюсь в 4:30. Не тревожно — просто светает рано, и комната становится другой. Заметил, что первые полчаса дня теперь самые тихие внутри. До того как включается телефон.',
    },
    {
      date: '12 июл · 21:14',
      mood: { en: 'anxious', ru: 'тревога' },
      moodVar: '--mood-anxious',
      tags: [
        { en: 'call', ru: 'звонок' },
        { en: 'mom', ru: 'мама' },
        { en: 'family', ru: 'семья' },
      ],
      matters: 8,
      en: 'Call with mom. Started fine, then she said about my brother — that he doesn’t call. I noticed I started defending him again, though I had promised myself not to step in. I’ve been carrying it all week.',
      ru: 'Звонок с мамой. Сначала шёл хорошо, а потом она сказала про брата — что он не звонит. Я понял, что опять начал защищать его, хотя обещал себе не вмешиваться. Всю неделю после ношу это с собой.',
    },
    {
      date: '9 июл · 19:02',
      mood: { en: 'glad', ru: 'радость' },
      moodVar: '--mood-joy',
      tags: [
        { en: 'work', ru: 'работа' },
        { en: 'walk', ru: 'прогулка' },
      ],
      matters: 3,
      en: 'A day without dark thoughts. Just worked, walked along the embankment at lunch. I noticed that when I expect nothing particular from a day — it usually turns out better.',
      ru: 'День прошёл без чёрных мыслей. Просто работал, гулял в обед по набережной. Заметил, что когда не жду от дня ничего особенного — он обычно оказывается лучше.',
    },
  ];
  return (
    <div className="guest-sample guest-journal">
      <div className="stats-line tnum">
        <span>
          <span className="n">24</span> {L2({ en: 'entries', ru: 'записей' })}
        </span>
        <span className="sep">·</span>
        <span>
          {L2({ en: 'this month', ru: 'за месяц' })} <span className="n">9</span>
        </span>
      </div>
      {entries.map((e, i) => (
        <article key={i} className="gentry">
          <div className="gentry__body serif">{L2({ en: e.en, ru: e.ru })}</div>
          <div className="gentry__foot">
            <span className="gentry__date">{e.date}</span>
            <span className="chip">
              <span
                className="gdotm"
                style={{ background: `var(${e.moodVar})` }}
                aria-hidden="true"
              />
              {L2(e.mood)}
            </span>
            <div className="gentry__tags">
              {e.tags.map((tg, j) => (
                <span key={j} className="chip chip--dim">
                  {L2(tg)}
                </span>
              ))}
            </div>
            <span className="gentry__matter">
              {L2({ en: 'matters', ru: 'важно' })}
              <span className="bar">
                <i style={{ width: `${e.matters * 10}%` }} />
              </span>
              <span className="tnum">{e.matters}</span>
            </span>
          </div>
        </article>
      ))}
      <p className="guest-author-note">
        {L2({
          en: 'Mood and how much it matters are yours — the companion does not guess them.',
          ru: 'Настроение и важность отмечаете вы — компаньон их не угадывает.',
        })}
      </p>
    </div>
  );
}

/* ---------- routing (OD routing.html) ---------- */

function RoutingSample() {
  const { L2 } = useLang();
  // OD routing.html: BYOK→env→ollama→mock, 64% / $0.41 of $0.64, providers,
  // last fallback. Wrapped in .rt-wrap to reuse the existing scoped
  // .chain-dense / .budget styles (the real RoutingScreen uses the same).
  const providers: {
    name: string;
    hstat: { en: string; ru: string };
    state: 'healthy' | 'standby' | 'off';
    model: string;
    reqs: string;
    tokens: string;
    cost: string;
  }[] = [
    {
      name: 'OpenAI',
      hstat: { en: 'healthy', ru: 'здоров' },
      state: 'healthy',
      model: 'gpt-5-mini',
      reqs: '12',
      tokens: '18,402',
      cost: '$0.041',
    },
    {
      name: 'Anthropic',
      hstat: { en: 'healthy', ru: 'здоров' },
      state: 'healthy',
      model: 'claude-haiku-4.5',
      reqs: '6',
      tokens: '9,210',
      cost: '$0.027',
    },
    {
      name: 'Ollama · local',
      hstat: { en: 'standby', ru: 'ожидает' },
      state: 'standby',
      model: 'llama3.2:3b',
      reqs: '24',
      tokens: '31,022',
      cost: '$0.000',
    },
    {
      name: 'Google',
      hstat: { en: 'unavailable', ru: 'недоступно' },
      state: 'off',
      model: 'gemini-2.5-flash',
      reqs: '4',
      tokens: '6,840',
      cost: '$0.012',
    },
  ];
  return (
    <div className="guest-sample rt-wrap">
      <div className="rt-rows">
        <section className="panel panel--chain">
          <div className="panel__head">
            <span className="t">{L2({ en: 'Fallback chain', ru: 'Цепочка fallback' })}</span>
            <span className="sub">
              {L2({
                en: 'ordered · local fallback last',
                ru: 'по порядку · локальный резерв в конце',
              })}
            </span>
          </div>
          <div className="panel__body">
            <div className="chain-dense">
              <span className="chip chip--on">
                BYOK<span className="tag">{L2({ en: 'per turn', ru: 'по ходу' })}</span>
              </span>
              <span className="conn" aria-hidden="true" />
              <span className="chip">env</span>
              <span className="conn" aria-hidden="true" />
              <span className="chip">ollama</span>
              <span className="conn" aria-hidden="true" />
              <span className="chip chip--dim">mock</span>
            </div>
            <p className="chain-note">
              {L2({
                en: 'BYOK wins even with env keys present; the matching env candidate is skipped (skip-self). On 429/5xx/timeout it falls through silently. Budget is checked first.',
                ru: 'BYOK выигрывает даже при наличии env-ключей; подходящий по типу env-кандидат пропускается (skip-self). На 429/5xx/timeout — тихий переход к следующему. Бюджет проверяется первым.',
              })}
            </p>
          </div>
        </section>

        <section className="panel panel--budget">
          <div className="panel__head">
            <span className="t">{L2({ en: 'Monthly budget', ru: 'Месячный бюджет' })}</span>
            <span className="sub">
              {L2({ en: 'soft 80% · hard 100%', ru: 'soft 80% · hard 100%' })}
            </span>
          </div>
          <div className="panel__body">
            <div className="budget">
              <svg viewBox="0 0 120 120" aria-hidden="true">
                <circle
                  cx="60"
                  cy="60"
                  r="50"
                  fill="none"
                  stroke="var(--border)"
                  strokeWidth="10"
                />
                <circle
                  cx="60"
                  cy="60"
                  r="50"
                  fill="none"
                  stroke="var(--purple)"
                  strokeWidth="10"
                  strokeLinecap="round"
                  strokeDasharray="201 314"
                  transform="rotate(-90 60 60)"
                />
                <text
                  x="60"
                  y="64"
                  textAnchor="middle"
                  fontFamily="var(--mono)"
                  fontSize="22"
                  fill="var(--heading)"
                  fontWeight="300"
                >
                  64%
                </text>
              </svg>
              <div className="meta">
                <span className="pct tnum">$0.41</span>
                <span className="lbl tnum">
                  {L2({
                    en: 'of $0.64 limit · $0.23 left',
                    ru: 'из $0.64 лимита · $0.23 осталось',
                  })}
                </span>
                <div className="flags">
                  <span className="flag flag--warn">soft-warn ≥80%</span>
                  <span className="flag flag--stop">hard-stop ≥100%</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="panel panel--full">
          <div className="panel__head">
            <span className="t">{L2({ en: 'Providers', ru: 'Провайдеры' })}</span>
            <span className="sub">
              {L2({ en: 'config-derived health', ru: 'health по конфигурации' })}
            </span>
          </div>
          <table className="tbl rt-tbl">
            <thead>
              <tr>
                <th>{L2({ en: 'Provider', ru: 'Провайдер' })}</th>
                <th>Model</th>
                <th className="num">reqs</th>
                <th className="num">tokens</th>
                <th className="num">cost</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((p, i) => (
                <tr key={i}>
                  <td>
                    <span className="prov">
                      <span
                        className={`dot ${p.state === 'healthy' ? '' : p.state}`}
                        title={p.state}
                      />
                      {p.name}{' '}
                      <span className={`hstat${p.state === 'off' ? ' unavail' : ''}`}>
                        {L2(p.hstat)}
                      </span>
                    </span>
                  </td>
                  <td className="mono">{p.model}</td>
                  <td className="num">{p.reqs}</td>
                  <td className="num">{p.tokens}</td>
                  <td className="num">{p.cost}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel panel--full rt-lastfb">
          <div className="panel__head">
            <span className="t">{L2({ en: 'Last fallback', ru: 'Последний fallback' })}</span>
            <span className="sub">{L2({ en: 'process-local', ru: 'процесс-локально' })}</span>
          </div>
          <div className="panel__body">
            <code className="mono">
              env → ollama · {L2({ en: '2 turns ago', ru: '2 хода назад' })}
            </code>
            <span className="chain-note">
              {L2({ en: 'lost on restart', ru: 'теряется на рестарте' })}
            </span>
          </div>
        </section>
      </div>
      <p className="rt-footnote">
        {L2({
          en: 'health is configuration-derived, not live probing · configured=healthy · ollama-without-url=standby · removed-with-prior-usage=unavailable',
          ru: 'здоровье — конфигурация, не живой пинг · configured=healthy · ollama-without-url=standby · removed-with-prior-usage=unavailable',
        })}
      </p>
    </div>
  );
}

/* ---------- personas (real builtins, not OD demo names) ---------- */

function PersonaSample() {
  const { L2 } = useLang();
  // The app's real builtin PERSONAS — the actual companions a signed-in user
  // sees. Showing the real builtins is more honest than reproducing the OD
  // project's demo names (Lina/Tyoma/…), which don't exist in the app.
  return (
    <div className="guest-sample">
      <div className="pcards">
        {PERSONAS.filter((p) => p.id !== 'fam').map((p) => (
          <article key={p.id} className="pcard">
            <span className="pcard__av" style={{ background: p.grad }} aria-hidden="true" />
            <div>
              <div className="pcard__name">{p.name}</div>
              <div className="pcard__role">{L2(p.role)}</div>
            </div>
            <p className="pcard__desc">{L2(p.vibe)}</p>
          </article>
        ))}
      </div>
      <p className="limitline">
        <em>
          {L2({
            en: 'The companion is a voice, not a person. It doesn’t perform feelings.',
            ru: 'Компаньон — голос, не человек. Чувств не играет.',
          })}
        </em>
      </p>
    </div>
  );
}

/* ---------- family (OD family.html) ---------- */

function FamilySample() {
  const { L2 } = useLang();
  // OD family.html: Iris (owner) + Mira/Jonah/Theo (members), shared layer,
  // shared memories, disclaimer.
  const members: {
    name: { en: string; ru: string };
    role: { en: string; ru: string };
    you?: boolean;
    grad: string;
  }[] = [
    {
      name: { en: 'Iris', ru: 'Ирис' },
      role: { en: 'owner', ru: 'владелец' },
      you: true,
      grad: 'linear-gradient(135deg,#8b78ff,#533afd)',
    },
    {
      name: { en: 'Mira', ru: 'Мира' },
      role: { en: 'member', ru: 'участник' },
      grad: 'linear-gradient(135deg,#5fd0c0,#2a9d8f)',
    },
    {
      name: { en: 'Jonah', ru: 'Йона' },
      role: { en: 'member', ru: 'участник' },
      grad: 'linear-gradient(135deg,#f5b96a,#d97706)',
    },
    {
      name: { en: 'Theo', ru: 'Тео' },
      role: { en: 'member', ru: 'участник' },
      grad: 'linear-gradient(135deg,#f08a7a,#c0442f)',
    },
  ];
  const shared: {
    txt: { en: string; ru: string };
    src: { en: string; ru: string };
    sal: string;
    donor?: boolean;
    ro?: boolean;
    sup?: boolean;
  }[] = [
    {
      txt: {
        en: "Thursday's argument was about Iris's work, not about Mitya.",
        ru: 'Ссора в четверг была о работе Ирис, не о Мите.',
      },
      src: { en: 'chain · Jul 14 · by: Mira', ru: 'цепочка · 14 июля · автор: Мира' },
      sal: '0.74',
    },
    {
      txt: {
        en: 'Jonah sleeps poorly when the house is tense.',
        ru: 'Йона плохо спит, когда в доме напряжение.',
      },
      src: { en: 'atomic · Jul 14 · by: Jonah', ru: 'атомарная · 14 июля · автор: Йона' },
      sal: '0.61',
    },
    {
      txt: {
        en: 'The family has dinner together on Sundays — a rule since June.',
        ru: 'Семья ужинает вместе по воскресеньям — правило с июня.',
      },
      src: { en: 'atomic · Jul 2 · by: Iris', ru: 'атомарная · 2 июля · автор: Ирис' },
      sal: '0.48',
    },
    {
      txt: {
        en: 'Theo reacts to shouting by leaving the room.',
        ru: 'Тео реагирует на крик уходом в другую комнату.',
      },
      src: { en: 'share from Theo · Jul 10', ru: 'шер от Тео · 10 июля' },
      sal: '0.55',
      donor: true,
      ro: true,
    },
    {
      txt: {
        en: 'Arguments used to end at dinner. Now they drag on.',
        ru: 'Раньше ссоры заканчивались за ужином. Сейчас затягиваются.',
      },
      src: { en: 'atomic · Jul 5 · by: Iris', ru: 'атомарная · 5 июля · автор: Ирис' },
      sal: '0.57',
    },
  ];
  return (
    <div className="guest-sample guest-family">
      <div className="comp-row" aria-label="Family composition">
        <div className="avatars" aria-hidden="true">
          {members.map((m, i) => (
            <span key={i} className="av" style={{ background: m.grad }} />
          ))}
        </div>
        <span className="lbl tnum">{L2({ en: '4 members', ru: '4 участника' })}</span>
        <span className="sep" aria-hidden="true" />
        <span className="shared">
          <span className="dot" aria-hidden="true" />
          {L2({ en: 'shared layer', ru: 'общий слой' })}
        </span>
      </div>

      <div className="mems">
        {members.map((m, i) => (
          <div key={i} className="mem">
            <span className="av" style={{ background: m.grad }} aria-hidden="true" />
            <div>
              <div className="mem__name">
                {L2(m.name)}
                {m.you && <span className="you-tag">{L2({ en: 'you', ru: 'вы' })}</span>}
              </div>
              <div className="mem__role">{L2(m.role)}</div>
            </div>
            <div className="mem__access">
              <span className="chip">{L2({ en: 'solo + shared', ru: 'свой + общий' })}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="panel__head">
          <span className="t">{L2({ en: 'Family memories', ru: 'Семейные памяти' })}</span>
          <span className="sub tnum">
            {L2({ en: '6 memories · 3 chains', ru: '6 памятей · 3 цепочки' })}
          </span>
        </div>
        <div className="panel__body" style={{ padding: 0 }}>
          {shared.map((s, i) => (
            <div key={i} className={`smem-row${s.donor ? ' donor' : ''}`}>
              <div>
                <div className="txt">{L2(s.txt)}</div>
                <div className="src">{L2(s.src)}</div>
              </div>
              <div className="right">
                <span className="sal tnum">{s.sal}</span>
                {s.ro ? (
                  <span className="ro">{L2({ en: 'read-only', ru: 'только чтение' })}</span>
                ) : s.sup ? (
                  <span className="chip chip--dim">{L2({ en: 'superseded', ru: 'заменено' })}</span>
                ) : (
                  <span className="chip chip--on">{L2({ en: 'active', ru: 'актуально' })}</span>
                )}
              </div>
            </div>
          ))}
        </div>
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
        <strong>
          {L2({
            en: 'The family therapist is not a licensed clinician. In crisis — 112 / 911.',
            ru: 'Семейный психолог — не лицензированный терапевт. При кризисах — 112 / 911.',
          })}
        </strong>
      </p>
    </div>
  );
}
