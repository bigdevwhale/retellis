'use client';

import { type ReactNode, createContext, useContext, useEffect, useMemo, useState } from 'react';

export type Lang = 'en' | 'ru';
export type Localized = { en: string; ru: string };

export const I18N: Record<string, { en: string; ru: string }> = {
  'brand.tag': { en: 'open · calm', ru: 'открытый · спокойный' },
  'rail.collapse': { en: 'Collapse', ru: 'Свернуть' },
  'rail.connected': { en: 'Connected', ru: 'Подключено' },
  'rail.theme': { en: 'Toggle theme', ru: 'Сменить тему' },

  'nav.newchat': { en: 'New chat', ru: 'Новый чат' },
  'nav.home': { en: 'Home', ru: 'Главная' },
  'nav.chat': { en: 'Chat', ru: 'Чат' },
  'nav.companions': { en: 'Companions', ru: 'Собеседники' },
  'nav.gallery': { en: 'Gallery', ru: 'Галерея' },
  'nav.create': { en: 'Create your own', ru: 'Создать своего' },
  'nav.memories': { en: 'Memories', ru: 'Воспоминания' },
  'nav.family': { en: 'Family', ru: 'Семья' },
  'nav.practices': { en: 'Practices', ru: 'Практики' },
  'nav.breathing': { en: 'Breathing', ru: 'Дыхание' },
  'nav.meditation': { en: 'Meditation', ru: 'Медитация' },
  'nav.plan': { en: 'Your plan', ru: 'Тариф' },
  'nav.advanced': { en: 'Advanced', ru: 'Расширенные' },
  'nav.routing': { en: 'Routing & budget', ru: 'Маршруты и бюджет' },
  'nav.keys': { en: 'Keys & setup', ru: 'Ключи и настройка' },
  'nav.settings': { en: 'Settings', ru: 'Настройки' },

  'home.badge': { en: 'Open-source · BYOK', ru: 'Открытый код · свои ключи' },
  'home.h1': { en: 'A quiet space to think.', ru: 'Тихое место для раздумий.' },
  'home.p': {
    en: "An open-source AI companion for calm, clarity, and inner peace. Bring your own keys, or let us handle it — either way, it's yours.",
    ru: 'ИИ-собеседник с открытым кодом для спокойствия, ясности и внутреннего равновесия. Подключите свои ключи или доверьте всё нам — в любом случае это ваше пространство.',
  },
  'home.cta.start': { en: 'Get started — free', ru: 'Начать — бесплатно' },
  'home.cta.plans': { en: 'See plans', ru: 'Посмотреть тарифы' },
  'home.feat.title': { en: 'Why Stillside', ru: 'Почему Stillside' },
  'home.how.title': { en: 'How it works', ru: 'Как это работает' },
  'home.closing': { en: 'Less noise. More you.', ru: 'Меньше шума. Больше вас.' },

  // --- Landing page (port of the Open Design marketing landing) ---
  // Bilingual copy is keyed; unilingual literals (flowline chips, BYOK ключ /
  // env / ollama / mock, mono labels, prices, 64%, $0.41, Free/Plus/Pro, the
  // "Stillside · open-source · MIT" tagline) live as constants in HomeScreen.
  'landing.hero.badge': { en: 'Open-source · BYOK', ru: 'Открытый код · свои ключи' },
  'landing.hero.h1': { en: 'A quiet space to think.', ru: 'Тихое место для раздумий.' },
  'landing.hero.sub': {
    en: "An AI companion, a journal, breathing and meditation — open-source, with your keys or ours. Either way, it's yours.",
    ru: 'ИИ-собеседник, дневник, дыхание и медитация — открытым кодом, с вашими ключами или под нашим присмотром. В любом случае это ваше пространство.',
  },
  'landing.hero.cta.start': { en: 'Start — free', ru: 'Начать — бесплатно' },
  'landing.hero.cta.plans': { en: 'See plans', ru: 'Посмотреть тарифы' },
  'landing.hero.bubble.who': {
    en: 'companion · therapist',
    ru: 'компаньон · терапевт',
  },
  'landing.hero.bubble.msg': {
    en: "I'm here. Tell me how you got to this.",
    ru: 'Я рядом. Расскажи, как дошло до этого.',
  },

  'landing.diff.eyebrow': {
    en: '02 · what’s different',
    ru: '02 · отличие',
  },
  'landing.diff.h2': { en: 'Not another AI chat', ru: 'Не очередной AI-чат' },
  'landing.diff.a.h3': { en: 'Plain AI chat', ru: 'Обычный AI-чат' },
  'landing.diff.a.p': {
    en: 'Forgets context, replies go cold over time, voice drifts, keys live with the vendor.',
    ru: 'Забывает контекст, ответы выхолащиваются со временем, голос плывёт, ключи у вендора.',
  },
  'landing.diff.b.p': {
    en: 'Event-chain memory with emotional salience — remembers what matters without cooling. A persona block injected every turn — voice doesn’t drift. Keys are yours, encrypted on-device.',
    ru: 'Event-chain память с эмоциональной значимостью — помнит важное, не охлаждая. Persona-блок инжектируется каждый ход — голос не дрейфит. Ключи только ваши, зашифрованы на устройстве.',
  },

  'landing.why.eyebrow': {
    en: '03 · why stillside',
    ru: '03 · почему stillside',
  },
  'landing.why.h2': {
    en: 'A whole space for self-care',
    ru: 'Полное пространство заботы',
  },
  'landing.why.a1.h3': {
    en: 'Memory that keeps empathy',
    ru: 'Память, бережная к эмпатии',
  },
  'landing.why.a1.p': {
    en: 'Remembers what matters without turning warm replies cold.',
    ru: 'Запоминает важное, не превращая тёплые ответы в сухие.',
  },
  'landing.why.a2.h3': {
    en: 'Your keys, your control',
    ru: 'Свои ключи — свой контроль',
  },
  'landing.why.a2.p': {
    en: 'Your API keys, sealed in transit and stored envelope-encrypted on the server. The server holds the decryption key and can read your key at reply time — NOT zero-knowledge; protects against a database dump, not the server operator.',
    ru: 'Свои API-ключи, запечатанные при передаче и хранящиеся на сервере в конвертном шифровании. Сервер хранит ключ расшифровки и может прочитать ваш ключ при ответе — НЕ нулевое разглашение; защита от дампа БД, а не от оператора сервера.',
  },
  'landing.why.a3.h3': { en: 'Family therapy', ru: 'Семейная терапия' },
  'landing.why.a3.p': {
    en: 'A therapist for up to four, with solo and joint sessions.',
    ru: 'Психолог для семьи до четырёх человек, с личными и общими сессиями.',
  },
  'landing.why.a4.h3': {
    en: 'Companions you can shape',
    ru: 'Компаньоны, которые вы создаёте',
  },
  'landing.why.a4.p': {
    en: 'Five ready-made, or your own — tone, warmth, pace.',
    ru: 'Пять готовых или свой с нуля — тон, теплоту, темп.',
  },
  'landing.why.b1.h4': { en: 'Journal', ru: 'Дневник' },
  'landing.why.b1.p': {
    en: 'A quiet diary apart from chat. Mood, tags, search.',
    ru: 'Тихий дневник отдельно от чата. Настроение, теги, поиск.',
  },
  'landing.why.b2.h4': {
    en: 'Breathing & meditation',
    ru: 'Дыхание и медитация',
  },
  'landing.why.b2.p': {
    en: 'Box / 4-7-8 breathing and a meditation timer. Offline.',
    ru: 'Дыхание box / 4-7-8 и таймер медитации. Работает офлайн.',
  },
  'landing.why.b3.h4': { en: 'Calm by design', ru: 'Спокойствие в каждой детали' },
  'landing.why.b3.p': {
    en: 'Gentle pacing, no notifications shouting at you.',
    ru: 'Мягкий темп, без навязчивых уведомлений.',
  },
  'landing.why.b4.h4': {
    en: 'Private by default',
    ru: 'Приватность по умолчанию',
  },
  'landing.why.b4.p': {
    en: 'Encrypted at rest. We never train on your conversations.',
    ru: 'Шифрование на диске. Не обучаем на ваших разговорах.',
  },

  'landing.how.eyebrow': {
    en: '04 · how it works',
    ru: '04 · как это работает',
  },
  'landing.how.h2': { en: 'Four quiet steps', ru: 'Четыре тихих шага' },
  'landing.how.s1.h4': { en: 'Pick a companion', ru: 'Выберите компаньона' },
  'landing.how.s1.p': {
    en: 'Therapist, friend, coach — or your own.',
    ru: 'Терапевт, друг, коуч — или свой.',
  },
  'landing.how.s2.h4': { en: 'Speak or write', ru: 'Говорите или пишите' },
  'landing.how.s2.p': {
    en: 'Text or voice. Whichever is easier today.',
    ru: 'Текстом или голосом. Как сегодня проще.',
  },
  'landing.how.s3.h4': {
    en: 'It remembers what matters',
    ru: 'Запоминает важное',
  },
  'landing.how.s3.p': {
    en: 'History builds itself, no fresh start each time.',
    ru: 'История собирается сама, без старта с нуля.',
  },
  'landing.how.s4.h4': { en: 'Breathe and write', ru: 'Дышите и записывайте' },
  'landing.how.s4.p': {
    en: 'Journal, breathing, meditation — right beside.',
    ru: 'Дневник, дыхание, медитация — рядом.',
  },

  'landing.tech.eyebrow': {
    en: '05 · for those who scroll this far',
    ru: '05 · для тех, кто доскроллил',
  },
  'landing.tech.h2': {
    en: 'Under the hood — BYOK and routing',
    ru: 'Под капотом — BYOK и роутинг',
  },
  'landing.tech.chain.title': {
    en: 'Routing chain',
    ru: 'Цепочка маршрутизации',
  },
  'landing.tech.chain.note': {
    en: 'BYOK wins even with env keys present; the env candidate matching its kind is skipped. On 429/5xx/timeout it falls through silently. Budget is checked first.',
    ru: 'BYOK выигрывает даже при наличии env-ключей; подходящий по типу env-кандидат пропускается. На 429/5xx/timeout — тихий переход к следующему. Бюджет проверяется первым.',
  },
  'landing.tech.table.provider': { en: 'Provider', ru: 'Провайдер' },
  'landing.tech.budget.title': { en: 'Monthly budget', ru: 'Месячный бюджет' },
  'landing.tech.budget.lbl': { en: 'of $0.64 limit', ru: 'из лимита $0.64' },
  'landing.tech.budget.note': {
    en: 'Soft-warn ≥80% — the turn proceeds normally. Hard-stop ≥100% — real providers are skipped and the local fallback serves the turn.',
    ru: 'Soft-warn ≥80% — ход идёт нормально. Hard-stop ≥100% — реальные провайдеры пропускаются, ход обслуживает локальный резерв.',
  },
  'landing.tech.foot3': {
    en: 'key zeroized after the turn',
    ru: 'ключ zeroized после хода',
  },

  'landing.pricing.eyebrow': { en: '06 · pricing', ru: '06 · цены' },
  'landing.pricing.h2': {
    en: 'Open — or with us looking after it',
    ru: 'Открыто — или с нашим присмотром',
  },
  'landing.pricing.free.unit': { en: '/ forever', ru: '/ навсегда' },
  'landing.pricing.plus.unit': { en: '/ mo', ru: '/ мес' },
  'landing.pricing.pro.unit': { en: '/ mo', ru: '/ мес' },
  'landing.pricing.free.f1': {
    en: 'Everything open, your keys',
    ru: 'Всё открыто, свои ключи',
  },
  'landing.pricing.free.f2': {
    en: 'Journal, breathing, meditation',
    ru: 'Дневник, дыхание, медитация',
  },
  'landing.pricing.free.f3': {
    en: 'Local or self-hosted',
    ru: 'Локально или self-hosted',
  },
  'landing.pricing.plus.f1': {
    en: 'Hosted keys — none of your own',
    ru: 'Хостинг-ключи — без своих',
  },
  'landing.pricing.plus.f2': {
    en: 'Priority routing across 6 models',
    ru: 'Приоритетный роутинг по 6 моделям',
  },
  'landing.pricing.plus.f3': {
    en: '90-day memory, voice',
    ru: 'Память 90 дней, голос',
  },
  'landing.pricing.pro.f1': {
    en: 'Unlimited memory, your prompts',
    ru: 'Безлимит памяти, свои промпты',
  },
  'landing.pricing.pro.f2': {
    en: 'Priority Opus / GPT-5',
    ru: 'Приоритетные Opus / GPT-5',
  },
  'landing.pricing.pro.f3': {
    en: 'Dashboard, 1:1 onboarding',
    ru: 'Дашборд, 1:1 онбординг',
  },
  'landing.pricing.free.cta': { en: 'Start', ru: 'Начать' },
  'landing.pricing.plus.cta': { en: 'Choose Plus', ru: 'Выбрать Plus' },
  'landing.pricing.pro.cta': { en: 'Choose Pro', ru: 'Выбрать Pro' },
  'landing.pricing.note': {
    en: 'On self-hosted, billing is hidden.',
    ru: 'На self-hosted биллинг скрыт.',
  },

  'landing.limits.eyebrow': {
    en: '07 · honest limits',
    ru: '07 · честные оговорки',
  },
  'landing.limits.h2': {
    en: 'Disclose, don’t perform',
    ru: 'Disclose, don’t perform',
  },
  'landing.limits.1.lead': {
    en: 'The family therapist is not a licensed clinician.',
    ru: 'Семейный психолог — не лицензированный терапевт.',
  },
  'landing.limits.1.body': {
    en: 'In crisis, abuse, or suicidal thoughts — emergency services (112 / 911).',
    ru: 'При кризисах, насилии, суицидальных мыслях — экстренные службы (112 / 911).',
  },
  'landing.limits.2.lead': {
    en: 'Breathing and meditation are standalone tools',
    ru: 'Дыхание и медитация — standalone-инструменты',
  },
  'landing.limits.2.body': {
    en: ', not a replacement for therapy; fully local.',
    ru: ', не замена терапии; работают полностью локально.',
  },
  'landing.limits.3.lead': { en: 'Journal:', ru: 'Дневник:' },
  'landing.limits.3.body': {
    en: ' mood and “how much it matters” are yours, not generated; the companion doesn’t infer emotions for you.',
    ru: ' настроение и «насколько важно» — авторские, не генерируются; компаньон не додумывает эмоции за вас.',
  },
  'landing.limits.4.lead': {
    en: 'Your API key is sealed in transit and stored envelope-encrypted on the server',
    ru: 'Ваш API-ключ запечатывается при передаче и хранится на сервере в конвертном шифровании',
  },
  'landing.limits.4.body': {
    en: ' — the server holds the decryption key and can read your key at reply time. This is NOT zero-knowledge: it protects against a database dump, not the server operator. The key is decrypted in memory only for the reply and zeroized after. Because keys live on the server, they survive a browser-data wipe and work across your devices.',
    ru: ' — сервер хранит ключ расшифровки и может прочитать ваш ключ при ответе. Это НЕ нулевое разглашение: защита от дампа БД, а не от оператора сервера. Ключ расшифровывается в памяти только для ответа и затем обнуляется. Поскольку ключи на сервере, они переживут очистку данных браузера и работают на всех ваших устройствах.',
  },

  'landing.closing.h2': {
    en: 'Less noise. More you.',
    ru: 'Меньше шума. Больше вас.',
  },

  'landing.foot.source': { en: 'Source', ru: 'Код' },
  'landing.foot.docs': { en: 'Docs', ru: 'Документация' },
  'landing.foot.security': { en: 'Security', ru: 'Безопасность' },
  'landing.foot.pricing': { en: 'Pricing', ru: 'Цены' },
  'landing.foot.contact': { en: 'Contact', ru: 'Контакты' },

  'onb.title': { en: 'Welcome', ru: 'Добро пожаловать' },
  'onb.h1': {
    en: 'A quiet space to think, on your own keys.',
    ru: 'Тихое место для раздумий — на ваших ключах.',
  },
  'onb.p': {
    en: 'Stillside is an open-source AI that listens, reflects, and remembers — for calm, clarity, and inner peace. Bring your own keys, or let us handle it. Either way, it’s yours.',
    ru: 'Stillside — это ИИ с открытым кодом, который слушает, помогает осмыслить и запоминает — ради спокойствия, ясности и внутреннего равновесия. Подключите свои ключи или доверьте всё нам. В любом случае это ваше.',
  },
  'onb.c1.title': { en: 'Connect a provider', ru: 'Подключите провайдера' },
  'onb.c1.desc': {
    en: 'Pick one to start. Add more and chain them as fallbacks later.',
    ru: 'Выберите одного, чтобы начать. Позже можно добавить ещё и выстроить цепочку запасных.',
  },
  'onb.p.openai': { en: 'gpt-5-mini, gpt-5', ru: 'gpt-5-mini, gpt-5' },
  'onb.p.or': { en: 'many models', ru: 'много моделей' },
  'onb.p.ollama': { en: 'local · offline', ru: 'локально · офлайн' },
  'onb.c2.title': { en: 'Add your key', ru: 'Добавьте ключ' },
  'onb.c2.desc': {
    en: 'Sealed in transit to the server and stored envelope-encrypted on the server. The server holds the decryption key and can read your key at reply time — NOT zero-knowledge; it protects against a database dump, not the server operator.',
    ru: 'Запечатывается при передаче на сервер и хранится на сервере в конвертном шифровании. Сервер хранит ключ расшифровки и может прочитать ваш ключ при ответе — НЕ нулевое разглашение; защита от дампа БД, а не от оператора сервера.',
  },
  'onb.summary.title': {
    en: 'Your provider',
    ru: 'Ваш провайдер',
  },
  'onb.summary.provider': {
    en: 'Provider',
    ru: 'Провайдер',
  },
  'onb.switch': {
    en: 'Switch provider',
    ru: 'Сменить провайдера',
  },
  'onb.model.cbx.use': {
    en: 'Use',
    ru: 'Использовать',
  },
  'onb.model.cbx.empty': {
    en: 'No matches — press Enter to use as custom',
    ru: 'Нет совпадений — Enter, чтобы ввести своё',
  },
  'onb.key': { en: 'API key', ru: 'API-ключ' },
  'onb.reveal': { en: 'Hold to reveal', ru: 'Удерживайте, чтобы показать' },
  'onb.hide': { en: 'Hide', ru: 'Скрыть' },
  'onb.showpass': { en: 'Show', ru: 'Показать' },
  'onb.hidepass': { en: 'Hide', ru: 'Скрыть' },
  'onb.keyhelp': {
    en: 'Sealed to the server session key (ECDH) once, then envelope-encrypted at rest on the server. The server holds the decryption key and can read your key at reply time.',
    ru: 'Однократно запечатывается сессионным ключом сервера (ECDH), затем хранится на сервере в конвертном шифровании. Сервер хранит ключ расшифровки и может прочитать ваш ключ при ответе.',
  },
  'onb.label': { en: 'Label', ru: 'Метка' },
  'onb.model': { en: 'Model', ru: 'Модель' },
  'onb.model.custom': { en: 'Custom…', ru: 'Своя…' },
  'onb.model.help': {
    en: 'Pick a model for this provider, or choose Custom to enter any model id.',
    ru: 'Выберите модель для этого провайдера или укажите «Своя», чтобы ввести любой id модели.',
  },
  'onb.model.placeholder': {
    en: 'model id, e.g. gpt-4o-mini',
    ru: 'id модели, напр., gpt-4o-mini',
  },
  'onb.embed': { en: 'Semantic memory (embeddings)', ru: 'Семантическая память (embeddings)' },
  'onb.embed.on': { en: 'Enable', ru: 'Включить' },
  'onb.embed.off': { en: 'Turn off', ru: 'Выключить' },
  'fam.key.embed.placeholder': {
    en: 'Embedding model for semantic memory (optional, e.g. text-embedding-3-small)',
    ru: 'Embedding-модель для семантической памяти (опционально, напр., text-embedding-3-small)',
  },
  'onb.embed.help': {
    en: 'Recall matches by meaning using this embedding model with your key (one small extra call per message). Off — or on any error — it falls back to built-in offline matching.',
    ru: 'Recall сопоставляет по смыслу через эту embedding-модель с вашим ключом (один небольшой доп. вызов на сообщение). Выключено — или при любой ошибке — работает встроенное офлайн-сопоставление.',
  },
  'onb.baseurl': { en: 'Endpoint URL', ru: 'URL эндпоинта' },
  'onb.baseurl.help': {
    en: 'Custom endpoint for this provider. Leave blank to use the provider default.',
    ru: 'Свой эндпоинт для этого провайдера. Оставьте пустым, чтобы использовать адрес по умолчанию.',
  },
  'onb.baseurl.ollama': {
    en: 'Local (http://localhost:11434) or Ollama Cloud (https://ollama.com — use this exact host, not cloud.ollama.com). Leave blank to use the server OLLAMA_BASE_URL. Ollama Cloud requires an API key from ollama.com/settings/keys.',
    ru: 'Локально (http://localhost:11434) или Ollama Cloud (https://ollama.com — используйте именно этот адрес, а не cloud.ollama.com). Пусто — сервер возьмёт OLLAMA_BASE_URL. Для Ollama Cloud нужен API-ключ с ollama.com/settings/keys.',
  },
  'onb.connect': { en: 'Connect →', ru: 'Подключить →' },
  'onb.connecting': { en: 'Encrypting & connecting…', ru: 'Шифруем и подключаем…' },
  'onb.connect.fail': {
    en: 'Could not reach the server. The key was not sent — try again.',
    ru: 'Не удалось связаться с сервером. Ключ не был отправлен — попробуйте ещё раз.',
  },
  'onb.reset': { en: 'Reset connection', ru: 'Сбросить подключение' },
  'onb.reset.hint': {
    en: 'Remove all connected keys on the server so you can choose a different provider, model, or endpoint.',
    ru: 'Удалить все подключённые ключи на сервере, чтобы выбрать другого провайдера, модель или эндпоинт.',
  },
  'onb.reset.confirm': {
    en: 'This removes all server-side provider keys. Continue?',
    ru: 'Это удалит все ключи провайдеров на сервере. Продолжить?',
  },
  'onb.reset.confirm.yes': { en: 'Wipe & reset', ru: 'Стереть и сбросить' },
  'onb.reset.confirm.no': { en: 'Cancel', ru: 'Отмена' },
  'onb.success': {
    en: 'Connected. Your key is sealed in transit and stored envelope-encrypted on the server. The server holds the decryption key and can read it at reply time — NOT zero-knowledge; it protects against a database dump, not the server operator.',
    ru: 'Подключено. Ключ запечатан при передаче и хранится на сервере в конвертном шифровании. Сервер хранит ключ расшифровки и может прочитать его при ответе — НЕ нулевое разглашение; защита от дампа БД, а не от оператора сервера.',
  },
  'onb.alt': {
    en: "Don't have a key, or don't want to manage one?",
    ru: 'Нет ключа или не хочется возиться с ним?',
  },
  'onb.alt.link': { en: 'Use a hosted plan →', ru: 'Выбрать облачный тариф →' },
  'onb.c3.title': { en: 'Pick a companion to start', ru: 'Выберите собеседника' },
  'onb.c3.desc': {
    en: 'Five are ready out of the box. Customize any of them, or build your own in the Companions tab.',
    ru: 'Пять готовы из коробки. Настройте любого из них или создайте своего во вкладке «Собеседники».',
  },
  'onb.start': { en: 'Start chatting →', ru: 'Начать чат →' },

  'chat.convos': { en: 'Conversations', ru: 'Диалоги' },
  'chat.delete': { en: 'Delete conversation', ru: 'Удалить диалог' },
  'chat.delete.confirm': {
    en: 'Delete this conversation? Its messages are removed on the server too. Derived memories persist. This cannot be undone.',
    ru: 'Удалить этот диалог? Его сообщения будут удалены и на сервере. Выведенные воспоминания сохранятся. Необратимо.',
  },
  // I35: undo-window delete. The native window.confirm is gone — the row is
  // removed optimistically and an Undo toast offers a 6s grace period before
  // the server DELETE commits. A server failure surfaces chat.delete.failed.
  'chat.deleted.toast': { en: 'Conversation deleted', ru: 'Диалог удалён' },
  'chat.undo': { en: 'Undo', ru: 'Отменить' },
  'chat.delete.failed': {
    en: "Couldn't delete the conversation on the server — it may reappear on refresh. Its messages were kept.",
    ru: 'Не удалось удалить диалог на сервере — он может появиться снова при обновлении. Сообщения сохранены.',
  },
  'np.title': { en: 'Start a new chat', ru: 'Новый чат' },
  'np.sub': { en: 'Choose who to talk with', ru: 'Выберите, с кем поговорить' },
  'np.close': { en: 'Close', ru: 'Закрыть' },
  // Phase 3 #17: a "Family therapy" shortcut in the New Chat picker. It
  // links to /family?tab=therapy (NOT startChatWith('fam')) so the /family
  // setup gate still applies — a family LLM key must exist first.
  'np.family.title': { en: 'Family therapy', ru: 'Семейная терапия' },
  'np.family.sub': {
    en: 'A shared session with the whole family — set up the family first.',
    ru: 'Общая сессия со всей семьёй — сначала настройте семью.',
  },
  'np.family.open': { en: 'Open family therapy →', ru: 'Открыть семейную терапию →' },
  'chat.autospeak': { en: 'Auto-speak replies', ru: 'Озвучивать ответы' },
  'chat.memory': { en: 'Memory on', ru: 'Память вкл.' },
  'chat.memory.on': { en: 'Memory on', ru: 'Память вкл.' },
  'chat.memory.off': { en: 'Memory off', ru: 'Память выкл.' },
  'chat.memory.tip': {
    en: 'Whether the companion recalls past events and memories when replying. Extraction still happens either way.',
    ru: 'Будет ли собеседник опираться на прошлые события и воспоминания в ответе. Само извлечение памяти происходит в любом случае.',
  },
  'chat.voice': { en: 'Voice mode', ru: 'Голосовой режим' },
  // Composer key indicator (OD `keyind`): an honest read of the active key
  // source. Never shows a fake key fingerprint — the client is zero-knowledge
  // (the opaque key_handle is NOT the key), so we surface the provider kind +
  // "connected"/"no key" + a change link instead of `sk-•••3a2f`.
  'chat.keyind.connected': { en: 'connected', ru: 'подключён' },
  'chat.keyind.none': { en: 'no key', ru: 'нет ключа' },
  'chat.keyind.change': { en: 'change', ru: 'сменить' },
  // Thread meta line under the persona name (OD `thread__meta`). Honest flags:
  // the persona block IS injected every turn (architecture fact), and memory
  // on/off is real client state. No fabricated chain count.
  'chat.meta.injected': { en: 'persona: injected', ru: 'персона: встроена' },
  'chat.placeholder': {
    en: 'Write or speak to Aria… (Enter to send)',
    ru: 'Напишите или продиктуйте Арии… (Enter — отправить)',
  },
  'chat.listening': { en: 'Listening… speak naturally', ru: 'Слушаю… говорите естественно' },
  'chat.thinking': { en: 'Aria is thinking…', ru: 'Ария думает…' },
  'chat.keepgoing': {
    en: 'Tap send, or keep talking',
    ru: 'Нажмите «Отправить» или продолжайте говорить',
  },
  'chat.stop': { en: 'Stop', ru: 'Стоп' },
  'chat.voicefail': { en: 'Voice unavailable', ru: 'Голос недоступен' },
  'chat.speak': { en: 'Speak', ru: 'Озвучить' },
  'chat.copy': { en: 'Copy', ru: 'Копировать' },
  'chat.copied': { en: 'Copied', ru: 'Скопировано' },
  'chat.nokey': {
    en: 'No provider key is active right now — a local fallback will reply. Add a key in Onboarding to use your connected key.',
    ru: 'Сейчас нет активного ключа провайдера — ответит локальный резерв. Добавьте ключ в «Начале работы», чтобы использовать подключённый ключ.',
  },
  'chat.nokey.link': { en: 'Open Onboarding →', ru: 'Открыть «Начало работы» →' },
  'chat.fallback': {
    en: 'Provider fell back: {from} → {to} ({reason})',
    ru: 'Переключение провайдера: {from} → {to} ({reason})',
  },
  // Family chat header labels and helper text. The previous "1:1" / "Joint"
  // pair and the bare <select> with no title were cryptic; the strings below
  // surface the intent in plain language.
  'chat.family.mode.private': {
    en: 'Solo with the family therapist — your private 1:1.',
    ru: 'Соло с семейным терапевтом — личный 1:1.',
  },
  'chat.family.mode.shared': {
    en: 'Joint — what you say is shared with the whole family.',
    ru: 'Совместно — то, что вы напишете, увидит вся семья.',
  },
  'chat.family.mode.private.short': { en: 'Solo 1:1', ru: 'Соло 1:1' },
  'chat.family.mode.shared.short': { en: 'Joint', ru: 'Совместно' },
  'chat.family.member.label': { en: 'Member', ru: 'Участник' },
  // Phase 3 #10/#11/#12: the family in-chat session surfaces intent in plain
  // language instead of a bare "Member:" label + a cryptic solo/joint toggle.
  'chat.family.speaking_as.label': { en: 'Speaking as:', ru: 'От чьего лица:' },
  'chat.family.speaking_as.helper': {
    en: 'Which member you are speaking as right now.',
    ru: 'От чьего лица вы пишете сейчас.',
  },
  'chat.family.joint.helper': {
    en: 'Joint mode — the whole family; the member pick is ignored.',
    ru: 'Совместный режим — вся семья; выбор участника игнорируется.',
  },
  'chat.family.joint.placeholder': { en: '— whole family —', ru: '— вся семья —' },
  'chat.family.mode.explain': {
    en: 'Solo 1:1 — private recall for this member. Joint — shared family recall.',
    ru: 'Соло 1:1 — личные воспоминания участника. Совместно — общие семейные воспоминания.',
  },
  'chat.family.settings_link': { en: 'Family settings', ru: 'Семейные настройки' },
  // Phase 3 #13/#14/#15: map known llm/stream errors to plain localized
  // messages, mark mid-stream truncation, and gate the "backend running"
  // hint to real network failures only. Used by lib/llm-errors.ts.
  'chat.err.family_404': {
    en: 'Your family session changed — reopen the family chat.',
    ru: 'Семейная сессия сменилась — откройте семейный чат заново.',
  },
  'chat.err.family_visibility': {
    en: 'Family scope error — reopen the family chat.',
    ru: 'Ошибка области семьи — откройте семейный чат заново.',
  },
  'chat.err.session_conflict': {
    en: 'Session conflict — start a new family chat.',
    ru: 'Конфликт сессии — начните новый семейный чат.',
  },
  'chat.err.generic_code': {
    en: 'Something went wrong (code {code}).',
    ru: 'Что-то пошло не так (код {code}).',
  },
  'chat.err.network': {
    en: 'Could not reach the companion API. Is the backend running on :8000?',
    ru: 'Не удалось связаться с API. Бэкенд запущен на :8000?',
  },
  'chat.err.unknown': {
    en: 'Something went wrong. Try again.',
    ru: 'Что-то пошло не так. Попробуйте снова.',
  },
  'chat.err.truncated': { en: ' [message truncated]', ru: ' [сообщение обрезано]' },
  'chat.family.noprovider.banner': {
    en: 'No family LLM key yet. Add one in /family → Family key to enable the family chat.',
    ru: 'Семейного ключа LLM ещё нет. Добавьте его в /family → Семейный ключ, чтобы включить семейный чат.',
  },
  'chat.personal.nokey.banner': {
    en: 'No personal LLM key yet. Add one in Onboarding to enable the chat.',
    ru: 'Личного ключа LLM ещё нет. Добавьте его в «Начало работы», чтобы включить чат.',
  },
  'chat.placeholder.locked': {
    en: 'Chat is disabled — add a key in settings to start.',
    ru: 'Чат выключен — добавьте ключ в настройках, чтобы начать.',
  },

  // Family vault reset (owner-only; destructive). Shared by lib/reset.ts.
  'fam.vault.reset': {
    en: 'Reset? Wipe family keys',
    ru: 'Сбросить? Стереть семейные ключи',
  },
  'fam.vault.reset.hint': {
    en: 'Wipes all family provider keys on the server. The owner must re-add a family key to continue. Members will see no family key until then.',
    ru: 'Стирает все семейные ключи провайдера на сервере. Владельцу нужно заново добавить семейный ключ. Участники не увидят семейный ключ до тех пор.',
  },
  'fam.vault.reset.confirm.phrase': {
    en: 'Type the family name to confirm:',
    ru: 'Введите имя семьи для подтверждения:',
  },
  'fam.vault.reset.confirm.yes': { en: 'Wipe & reset', ru: 'Стереть и сбросить' },
  'fam.vault.reset.confirm.no': { en: 'Cancel', ru: 'Отмена' },
  'fam.vault.reset.success': {
    en: 'Family keys wiped. Add a new family key to continue.',
    ru: 'Семейные ключи стёрты. Добавьте новый семейный ключ, чтобы продолжить.',
  },
  'fam.vault.reset.fail': {
    en: "Couldn't wipe the family keys: {message}",
    ru: 'Не удалось стереть семейные ключи: {message}',
  },

  // /family top-level tab strip (Members | Therapy | Settings).
  'fam.tab.members': { en: 'Members', ru: 'Участники' },
  'fam.tab.therapy': { en: 'Therapy', ru: 'Терапия' },
  'fam.tab.settings': { en: 'Settings', ru: 'Настройки' },

  // OD family.html shell port: pagehead lede, composition row, members list
  // labels, and the honest disclaimer. All copy is structural — no fabricated
  // member names or memory rows (those stay backed by real familyMembers).
  'fam.lede': {
    en: 'A therapist for up to four. Solo and joint sessions.',
    ru: 'Психолог для семьи до четырёх. Личные и общие сессии.',
  },
  'fam.comp.members': { en: '{n} members', ru: '{n} участника' },
  'fam.comp.shared': { en: 'shared layer', ru: 'общий слой' },
  'fam.layer.title': { en: 'Memory layers', ru: 'Слои памяти' },
  'fam.layer.eyebrow': { en: 'shared and private', ru: 'общее и личное' },
  'fam.layer.shared': { en: 'shared', ru: 'общий' },
  'fam.layer.family': { en: 'family', ru: 'семья' },
  'fam.layer.private': { en: 'private', ru: 'личный' },
  'fam.layer.legend.shared': { en: 'family shared layer', ru: 'общий слой семьи' },
  'fam.layer.legend.priv': {
    en: 'private layer (private from others)',
    ru: 'личный слой (приватно от других)',
  },
  'fam.members.eyebrow': { en: 'up to four people', ru: 'до четырёх человек' },
  'fam.mem.access': { en: 'solo + shared', ru: 'свой + общий' },
  'fam.mem.role.owner': { en: 'owner', ru: 'владелец' },
  'fam.mem.role.member': { en: 'member', ru: 'участник' },
  'fam.mem.you': { en: 'you', ru: 'вы' },
  'fam.disc': {
    en: 'The family therapist is not a licensed clinician. In crisis — 112 / 911.',
    ru: 'Семейный психолог — не лицензированный терапевт. При кризисах — 112 / 911.',
  },

  // --- Guest informational showcases (OD feature tabs for signed-out visitors) ---
  // Each feature route renders one of these for a guest: OD pagehead + sample
  // content (clearly labelled "sample") + a sign-in CTA. The sample content is
  // illustrative demo data — it is never presented as the visitor's own.
  'guest.badge': { en: 'sample', ru: 'пример' },
  'guest.cta.title': { en: 'Sign in to use yours', ru: 'Войдите, чтобы использовать свои' },
  'guest.cta.note': {
    en: 'Bring your own keys or use ours. Keys are sealed in transit and stored envelope-encrypted on the server — the server holds the decryption key and can read your key at reply time (NOT zero-knowledge; protects against a database dump, not the server operator).',
    ru: 'Свои ключи или наши. Ключи запечатываются при передаче и хранятся на сервере в конвертном шифровании — сервер хранит ключ расшифровки и может прочитать ваш ключ при ответе (НЕ нулевое разглашение; защита от дампа БД, а не от оператора сервера).',
  },
  'guest.chat.lede': {
    en: 'A calm conversation with event-chain memory. It remembers what mattered, not just what you said.',
    ru: 'Спокойный разговор с памятью в виде цепочек событий. Помнит важное, а не только сказанное.',
  },
  'guest.memory.lede': {
    en: 'Memories are recalled as intact chains — what led to what, weighted by salience and recency.',
    ru: 'Воспоминания вспоминаются цепочками — что к чему привело, с весом значимости и давности.',
  },
  'guest.journal.lede': {
    en: 'A private journal with mood and tags — yours, never generated. Search and revisit what you wrote.',
    ru: 'Личный дневник с настроением и тегами — ваш, не сгенерированный. Поиск и возврат к написанному.',
  },
  'guest.practices.lede': {
    en: 'Breathing and meditation tools, fully offline. No account needed — use them now.',
    ru: 'Дыхание и медитация, полностью офлайн. Без аккаунта — можно пользоваться прямо сейчас.',
  },
  'guest.routing.lede': {
    en: 'The fallback chain that serves every turn — BYOK, then env keys, then Ollama, then a local fallback.',
    ru: 'Цепочка провайдеров на каждый ход — свои ключи, затем env, затем Ollama, затем локальный резерв.',
  },
  'guest.persona.lede': {
    en: 'Five ready-made companions or your own. Tone, warmth, pace — a deterministic, injected persona.',
    ru: 'Пять готовых компаньонов или свой. Тон, теплота, темп — детерминированный, внедряемый персонаж.',
  },
  'guest.family.lede': {
    en: 'A therapist for up to four. Solo and joint sessions, with a shared memory layer.',
    ru: 'Психолог для семьи до четырёх. Личные и общие сессии с общим слоем памяти.',
  },

  // /family primary view: link to the settings sub-page.
  'fam.settings.link': { en: 'Settings →', ru: 'Настройки →' },
  'fam.settings.title': { en: 'Family settings', ru: 'Настройки семьи' },

  // Family therapy entry surface on the /family Members tab. The 'fam'
  // persona is gated on a family LLM key, so the CTA is disabled (with a
  // clear hint) until the owner adds one.
  'fam.therapy.title': { en: 'Family therapy', ru: 'Семейная терапия' },
  'fam.therapy.sub': {
    en: 'A shared space where the family therapist companion talks to your whole family. The conversation is shared with all members. The companion is not a licensed family therapist — direct safety crises to emergency services (112 / 911) and qualified local professionals.',
    ru: 'Общее пространство, где семейный психолог разговаривает со всей семьёй. Диалог виден всем участникам. Компаньон — не лицензированный семейный терапевт: в острых кризисах обращайтесь в экстренные службы (112 / 911) и к квалифицированным специалистам.',
  },
  'fam.therapy.open': { en: 'Open family therapy →', ru: 'Открыть семейную терапию →' },

  // Phase 2 clarity: the Family key tab's provider form is gated only on
  // the viewer being the owner. This notice replaces the old dead-greyed
  // form so the user sees WHY it is inactive.
  'fam.key.form.nonowner_notice': {
    en: 'Only the family owner can add or change the family key — ask them.',
    ru: 'Только владелец семьи может добавить или сменить семейный ключ — обратитесь к нему.',
  },

  // Owner-customisable family therapist prompt — owner edits, every member
  // reads. The hard-coded "Disclose, don't perform" footer is appended
  // client-side so the owner can never drop the safety line.
  'fam.therapist_prompt.tab': { en: 'Therapist', ru: 'Терапевт' },
  'fam.therapist_prompt.title': {
    en: 'Family therapist prompt',
    ru: 'Промпт семейного психолога',
  },
  'fam.therapist_prompt.sub': {
    en: 'Tailor what the family therapist is told. Only the family owner can edit; every member can read what the therapist sees.',
    ru: 'Настройте, что семейный психолог получает в системном промпте. Редактировать может только владелец семьи; читать — все участники.',
  },
  'fam.therapist_prompt.section.focus': {
    en: 'Session focus',
    ru: 'Фокус сессии',
  },
  'fam.therapist_prompt.section.focus.tip': {
    en: 'The current concern — a school transition, a loss, a conflict to mediate. Empty = the open-ended baseline.',
    ru: 'Текущая тема — начало учебного года, потеря, конфликт для медиации. Пусто = открытая базовая линия.',
  },
  'fam.therapist_prompt.section.rules': { en: 'Family rules', ru: 'Правила семьи' },
  'fam.therapist_prompt.section.rules.tip': {
    en: 'Hard constraints — "no medical advice", "address each member by name", "never ask about X". Empty = no extra rules.',
    ru: 'Жёсткие ограничения — «без медицинских советов», «обращаться к каждому по имени», «никогда не спрашивать о X». Пусто = без дополнительных правил.',
  },
  'fam.therapist_prompt.section.context': { en: 'Family context', ru: 'Контекст семьи' },
  'fam.therapist_prompt.section.context.tip': {
    en: 'Who is in the family, ages, key situations. Pre-filled from your members — edit freely.',
    ru: 'Кто в семье, возраст, ключевые обстоятельства. Заполнено из состава семьи — отредактируйте как нужно.',
  },
  'fam.therapist_prompt.section.approach': { en: 'Approach', ru: 'Подход' },
  'fam.therapist_prompt.section.approach.tip': {
    en: 'Methodology — e.g. "reflect first, then offer a small frame". Empty = no extra approach.',
    ru: 'Метод — например, «сначала отражение, потом маленькая рамка». Пусто = без дополнительного подхода.',
  },
  'fam.therapist_prompt.section.focus.placeholder': {
    en: "What should the family therapist focus on right now? (e.g. 'new school year', 'a recent loss')",
    ru: 'На чём сосредоточиться прямо сейчас? (напр., «новый учебный год», «недавняя потеря»)',
  },
  'fam.therapist_prompt.section.rules.placeholder': {
    en: "Family-specific rules the therapist must follow. (e.g. 'never give medical advice', 'address each member by name')",
    ru: 'Особые правила для психолога. (напр., «не давать медицинских советов», «обращаться к каждому по имени»)',
  },
  'fam.therapist_prompt.section.approach.placeholder': {
    en: "How should the therapist approach sessions? (e.g. 'reflect first, then offer a small frame')",
    ru: 'Какой подход использовать? (напр., «сначала отражение, потом маленькая рамка»)',
  },
  'fam.therapist_prompt.preview.title': { en: 'Preview', ru: 'Предпросмотр' },
  'fam.therapist_prompt.preview.help': {
    en: 'This is the exact text the therapist will see. The "disclose, don’t perform" line is always appended — you can’t drop it.',
    ru: 'Это точный текст, который увидит психолог. Строка «будьте искренни, не играйте» всегда добавляется — её нельзя убрать.',
  },
  'fam.therapist_prompt.save': { en: 'Save prompt', ru: 'Сохранить промпт' },
  'fam.therapist_prompt.saved': {
    en: 'Saved. All family members can now see this prompt.',
    ru: 'Сохранено. Все участники семьи увидят этот промпт.',
  },
  'fam.therapist_prompt.clear': { en: 'Reset to built-in', ru: 'Сбросить к встроенному' },
  'fam.therapist_prompt.cleared': {
    en: 'Reset to the built-in baseline.',
    ru: 'Сброшено к встроенной базовой линии.',
  },
  'fam.therapist_prompt.audit': {
    en: 'Set by {name} · {date}',
    ru: 'Установлен {name} · {date}',
  },
  'fam.therapist_prompt.audit.builtin': {
    en: 'Built-in baseline (no custom prompt set).',
    ru: 'Встроенная базовая линия (настройка не задана).',
  },
  'fam.therapist_prompt.error.body_too_long': {
    en: 'Prompt is too long (max 8,000 characters).',
    ru: 'Промпт слишком длинный (макс. 8000 символов).',
  },

  'mem.title': { en: 'Memories', ru: 'Воспоминания' },
  'mem.sub': {
    en: 'Event-chain memory — what the companion has learned',
    ru: 'Цепочка событий в памяти — что узнал собеседник',
  },
  'mem.persona': { en: 'Persona', ru: 'Персонаж' },
  'mem.f.all': { en: 'All', ru: 'Все' },
  'mem.f.anxious': { en: 'anxious', ru: 'тревога' },
  'mem.f.hopeful': { en: 'hopeful', ru: 'надежда' },
  'mem.f.work': { en: 'work', ru: 'работа' },
  'mem.f.most': { en: 'matters most', ru: 'важнее всего' },
  'mem.loading': { en: 'Loading memories…', ru: 'Загружаем воспоминания…' },
  'mem.empty': {
    en: 'No memories yet. Send a message in chat and the companion will start remembering.',
    ru: 'Пока ничего нет. Напишите в чат — и собеседник начнёт запоминать.',
  },
  'mem.empty.cta': { en: 'Go to chat', ru: 'Перейти в чат' },
  'mem.recall': { en: 'Recall probe', ru: 'Проверка памяти' },
  'mem.recall.ph': {
    en: 'What does the companion remember about…',
    ru: 'Что собеседник помнит о…',
  },
  'mem.recall.go': { en: 'Recall', ru: 'Вспомнить' },
  'mem.recall.empty': {
    en: 'Nothing recalled for that query.',
    ru: 'По этому запросу ничего не нашлось.',
  },
  'mem.role.user': { en: 'they said', ru: 'вы сказали' },
  'mem.role.assistant': { en: 'companion said', ru: 'собеседник ответил' },
  'mem.salience': { en: 'salience', ru: 'значимость' },
  'mem.emo.title': {
    en: 'Emotional signal — auto-classified, may be imperfect',
    ru: 'Эмоциональный сигнал — автоклассификация, может ошибаться',
  },
  'mem.chain': { en: 'Recalled chain', ru: 'Цепочка' },
  // Atomic memories (display layer over the event chain) — synthesized facts,
  // not raw chat messages. Themes are derived from the memories' own tags.
  'mem.themes': { en: 'Themes', ru: 'Темы' },
  'mem.drawn': {
    en: 'drawn from {n} turn(s)',
    ru: 'из {n} реплик',
  },
  'mem.updated': { en: 'updated {when}', ru: 'обновлено {when}' },
  'mem.rel.today': { en: 'today', ru: 'сегодня' },
  'mem.rel.yesterday': { en: 'yesterday', ru: 'вчера' },
  'mem.rel.daysago': { en: '{n} days ago', ru: '{n} дн. назад' },
  'mem.rel.weeksago': { en: '{n} weeks ago', ru: '{n} нед. назад' },
  'mem.rel.monthsago': { en: '{n} months ago', ru: '{n} мес. назад' },
  // Cross-persona live memory shares — donor-initiated link, a reference not a
  // copy. The donor shares its memory INTO the receiver; receiver sees donor
  // memories (badged "shared from {name}") and the companion recalls them.
  'mem.share.title': { en: 'Share with another persona', ru: 'Поделиться с другой персоной' },
  'mem.share.sub': {
    en: 'A live link — the other persona recalls what this one knows. Revocable; nothing is copied.',
    ru: 'Живая связь — другой персонаж вспоминает то же, что этот. Можно отменить; ничего не копируется.',
  },
  'mem.share.pick': { en: 'Share with…', ru: 'Поделиться с…' },
  'mem.share.add': { en: 'Share', ru: 'Поделиться' },
  'mem.share.with': { en: 'Sharing with {name}', ru: 'Делится с {name}' },
  'mem.share.remove': { en: 'Remove', ru: 'Убрать' },
  'mem.share.empty': { en: 'Not sharing with anyone yet', ru: 'Пока ни с кем не делится' },
  'mem.share.from': { en: 'shared from {name}', ru: 'от {name}' },
  'mem.share.self': {
    en: "Can't share a persona with itself",
    ru: 'Нельзя поделиться с той же персоной',
  },
  'mem.wipe': { en: "Erase this persona's memory", ru: 'Стереть память этой персоны' },
  'mem.wipe.hint': {
    en: 'Deletes everything this persona has remembered — its message events, its derived memories, and any shares it sends to other personas. Facts other personas share into it remain until they revoke the share. This cannot be undone.',
    ru: 'Удаляет всё, что запомнила эта персона — её события сообщений, выведенные воспоминания и её исходящие шары. Факты, которыми другие персоны делятся с ней, остаются, пока они не отзовут шар. Необратимо.',
  },
  'mem.wipe.confirm': {
    en: 'Erase all memory for {name}? This cannot be undone.',
    ru: 'Стереть всю память для {name}? Это необратимо.',
  },
  'mem.wipe.confirm.yes': { en: 'Erase', ru: 'Стереть' },
  'mem.wipe.confirm.no': { en: 'Cancel', ru: 'Отмена' },
  // OD memory.html pagehead + segmented view toggle. The statrow counts are
  // honest (only memories + outgoing shares are known client-side; the server
  // exposes no "all chains" / "all events" list endpoint, so we don't fabricate
  // those counts).
  'mem.eyebrow': { en: 'Memory transparency', ru: 'Прозрачность памяти' },
  'mem.lede': {
    en: 'What your companion remembers. You can change or forget it.',
    ru: 'Что компаньон помнит о вас. Можно изменить или забыть.',
  },
  'mem.stat.memories': { en: 'memories', ru: 'памяти' },
  'mem.stat.shares': { en: 'shares', ru: 'шеры' },
  'mem.view.chains': { en: 'Chains', ru: 'Цепочки' },
  'mem.view.memories': { en: 'Memories', ru: 'Памяти' },
  'mem.view.shares': { en: 'Shares', ru: 'Шеры' },
  'mem.h.linked': { en: 'Linked threads', ru: 'Связанные нити' },
  'mem.h.atomic': { en: 'Atomic memories', ru: 'Атомарные памяти' },
  'mem.h.shares': { en: 'Outgoing shares', ru: 'Исходящие шеры' },
  'mem.danger.h': { en: 'Danger zone', ru: 'Опасная зона' },
  'mem.table.text': { en: 'Text', ru: 'Текст' },
  'mem.table.status': { en: 'Status', ru: 'Статус' },
  'mem.table.salience': { en: 'Salience', ru: 'Значимость' },
  'mem.table.source': { en: 'Source', ru: 'Источник' },
  'mem.table.date': { en: 'Date', ru: 'Дата' },
  'mem.st.active': { en: 'active', ru: 'актуально' },
  'mem.st.superseded': { en: 'superseded', ru: 'заменено' },
  'mem.st.shared': { en: 'shared · read-only', ru: 'чужое · только чтение' },

  // --- Journal: a quiet, read-first diary surface (separate from chat) ---
  // The user writes their own entries AND can seed one from a chat message
  // ("Save to journal"). mood + tags are authored by the user — the journal
  // surfaces them as-is, never generating affective claims ("disclose, don't
  // perform"). No exclamation marks (DESIGN.md §7).
  'nav.journal': { en: 'Journal', ru: 'Дневник' },
  // Short tab labels for the landing header's 7-tab nav (OD app chrome). The
  // full nav.* equivalents are longer ("Routing & budget" / "Companions") which
  // overflows a horizontal tab strip; these mirror the OD labels verbatim.
  'navtab.routing': { en: 'Routing', ru: 'Маршрутизация' },
  'navtab.personas': { en: 'Personas', ru: 'Персонажи' },
  'navtab.menu': { en: 'Menu', ru: 'Меню' },
  'journal.title': { en: 'Journal', ru: 'Дневник' },
  'journal.sub': {
    en: 'A quiet page for what you’ve lived through',
    ru: 'Тихая страница о том, что вы пережили',
  },
  'journal.composer.ph': { en: 'What’s on your mind…', ru: 'Что у вас на уме…' },
  'journal.title.ph': { en: 'Title (optional)', ru: 'Заголовок (необязательно)' },
  'journal.save': { en: 'Save', ru: 'Сохранить' },
  'journal.cancel': { en: 'Cancel', ru: 'Отмена' },
  'journal.edit': { en: 'Edit', ru: 'Изменить' },
  'journal.delete': { en: 'Delete', ru: 'Удалить' },
  'journal.delete.confirm': {
    en: 'Delete this entry? This cannot be undone.',
    ru: 'Удалить эту запись? Это необратимо.',
  },
  'journal.delete.confirm.yes': { en: 'Delete', ru: 'Удалить' },
  'journal.delete.confirm.no': { en: 'Keep', ru: 'Оставить' },
  'journal.search.ph': { en: 'Search your journal…', ru: 'Искать в дневнике…' },
  'journal.filter.mood': { en: 'All moods', ru: 'Все настроения' },
  'journal.filter.clear': { en: 'Clear filters', ru: 'Сбросить фильтры' },
  'journal.empty': {
    en: 'Nothing here yet. Write your first entry above.',
    ru: 'Пока пусто. Напишите первую запись выше.',
  },
  'journal.loading': { en: 'Loading your journal…', ru: 'Загружаем дневник…' },
  'journal.matters': { en: 'Matters to me', ru: 'Насколько важно' },
  'journal.matters.lvl': { en: 'level {n}', ru: 'уровень {n}' },
  'journal.mood': { en: 'Mood', ru: 'Настроение' },
  'journal.mood.ph': { en: 'one word, e.g. calm', ru: 'одно слово, напр., спокойно' },
  'journal.tags': { en: 'Tags', ru: 'Метки' },
  'journal.tags.ph': { en: 'add a tag, press Enter', ru: 'добавьте метку, нажмите Enter' },
  'journal.fromchat': { en: 'from chat with {name}', ru: 'из чата с {name}' },
  'journal.fromchat.bare': { en: 'from chat', ru: 'из чата' },
  'journal.saved.toast': { en: 'Saved to your journal', ru: 'Сохранено в дневник' },
  'journal.rel.today': { en: 'Today', ru: 'Сегодня' },
  'journal.rel.yesterday': { en: 'Yesterday', ru: 'Вчера' },
  'journal.chat.save': { en: 'Save to journal', ru: 'Сохранить в дневник' },

  // --- Cozy diary redesign — warm copy, no exclamation marks (DESIGN.md §7) ---
  'journal.greeting.morning': { en: 'Good morning', ru: 'Доброе утро' },
  'journal.greeting.day': { en: 'Good afternoon', ru: 'Добрый день' },
  'journal.greeting.evening': { en: 'Good evening', ru: 'Добрый вечер' },
  'journal.hero.line': {
    en: 'A quiet space to sit with yourself',
    ru: 'Тихое место, чтобы побыть с собой',
  },
  'journal.cta.write': { en: 'Write the day', ru: 'Записать день' },
  'journal.privacy': { en: 'Private · just for you', ru: 'Приватно · только для тебя' },
  'journal.streak.week': { en: 'entries this week · {n}', ru: 'записей за неделю: {n}' },
  'journal.feeling.ask': { en: 'How are you feeling?', ru: 'Как ты себя чувствуешь?' },
  'journal.ribbon.title': { en: 'This month', ru: 'Этот месяц' },
  'journal.recent.title': { en: 'Recent memories', ru: 'Последние воспоминания' },
  'journal.quote.title': { en: 'A thought for today', ru: 'Мысль на сегодня' },
  'journal.write.back': { en: 'Back to diary', ru: 'К дневнику' },
  'journal.write.heading': { en: 'Today’s entry', ru: 'Сегодняшняя запись' },
  'journal.write.prompt.placeholder': { en: 'Write as you feel…', ru: 'Пиши, как чувствуешь…' },
  'journal.empty.ill': {
    en: 'Nothing here yet — and that’s okay',
    ru: 'Здесь пока пусто — и это нормально',
  },
  'journal.empty.cta': { en: 'Write your first day', ru: 'Записать первый день' },
  // Soft prompt chips — clicking one opens the writer with the prompt as a guide.
  'journal.prompt.joy': { en: 'What brought you joy today?', ru: 'Что сегодня принесло радость?' },
  'journal.prompt.remember': {
    en: 'What do you want to remember?',
    ru: 'Что хочется запомнить?',
  },
  'journal.prompt.now': { en: 'What’s on your mind right now?', ru: 'О чём ты думаешь сейчас?' },
  // Mood quick-picker — writes the localized word into the same free-text mood field.
  'journal.mood.calm': { en: 'calm', ru: 'спокойно' },
  'journal.mood.joy': { en: 'joy', ru: 'радость' },
  'journal.mood.grateful': { en: 'grateful', ru: 'благодарность' },
  'journal.mood.hopeful': { en: 'hopeful', ru: 'надежда' },
  'journal.mood.tired': { en: 'tired', ru: 'усталость' },
  'journal.mood.anxious': { en: 'anxious', ru: 'тревога' },
  'journal.mood.sad': { en: 'sad', ru: 'грусть' },
  'journal.mood.neutral': { en: 'neutral', ru: 'ровно' },
  // A gentle, rotating daily quote (en + ru). One per day of the week.
  'journal.quote.0': {
    en: '“Tell me, what is it you plan to do with your one wild and precious life?” — Mary Oliver',
    ru: '«Скажи мне, что ты собираешься сделать со своей одной дикой и драгоценной жизнью?» — Мэри Оливер',
  },
  'journal.quote.1': {
    en: '“Go confidently in the direction of your dreams.” — Henry David Thoreau',
    ru: '«Иди уверенно в направлении своих мечтаний.» — Генри Дэвид Торо',
  },
  'journal.quote.2': {
    en: '“You are the sky. Everything else is just the weather.” — Pema Chödrön',
    ru: '«Ты — небо. Всё остальное — просто погода.» — Пема Чодрон',
  },
  'journal.quote.3': {
    en: '“Let everything happen to you: beauty and terror. Just keep going.” — Rainer Maria Rilke',
    ru: '«Позволь всему случаться с тобой: красоте и ужасу. Просто продолжай идти.» — Райнер Мария Рильке',
  },
  'journal.quote.4': {
    en: '“You have power over your mind, not outside events.” — Marcus Aurelius',
    ru: '«Ты властен над своим разумом, но не над событиями.» — Марк Аврелий',
  },
  'journal.quote.5': {
    en: '“The wound is the place where the Light enters you.” — Rumi',
    ru: '«Рана — это место, куда входит свет.» — Руми',
  },
  'journal.quote.6': {
    en: '“There is no greater agony than bearing an untold story inside you.” — Maya Angelou',
    ru: '«Нет большей муки, чем носить в себе нерассказанную историю.» — Майя Энджелоу',
  },
  // --- Diary redesign: hairline rhythm, month ribbon, states, writer moment.
  'journal.prompts.eyebrow': {
    en: 'If you’d like a place to start',
    ru: 'Если хочется с чего-то начать',
  },
  'journal.ribbon.count.days': {
    en: '{n} days with entries',
    ru: '{n} дней с записями',
  },
  'journal.ribbon.count.zero': { en: 'no entries yet', ru: 'пока нет записей' },
  'journal.week.days': {
    en: 'This week you wrote on {n} days',
    ru: 'На этой неделе вы писали {n} дн.',
  },
  'journal.readfull': { en: 'Read in full', ru: 'Читать полностью' },
  'journal.showless': { en: 'Show less', ru: 'Свернуть' },
  'journal.delete.confirm.q': { en: 'Delete this entry?', ru: 'Удалить эту запись?' },
  'journal.matters.note': {
    en: 'Your own dial — how much this matters to you. Nothing scores it for you.',
    ru: 'Твой собственный ориентир — насколько это важно для тебя. Никто не оценивает это за тебя.',
  },
  'journal.mood.label': { en: 'How would you name it?', ru: 'Как бы ты это назвал?' },
  'journal.mood.asst': {
    en: '(your word — never suggested)',
    ru: '(твоё слово — не подсказывается)',
  },
  'journal.draft.saved': { en: 'Draft saved', ru: 'Черновик сохранён' },
  'journal.error.msg': {
    en: 'Your entries didn’t load this time.',
    ru: 'Не удалось загрузить записи в этот раз.',
  },
  'journal.error.sub': {
    en: 'They’re still stored safely — nothing was lost. Trying again usually works.',
    ru: 'Они сохранены — ничего не потеряно. Обычно помогает попробовать ещё раз.',
  },
  'journal.error.retry': { en: 'Try again', ru: 'Попробовать снова' },
  'journal.empty.title': { en: 'Nothing written yet', ru: 'Пока ничего не записано' },
  'journal.empty.body': {
    en: 'Your first entry can be a single sentence. It stays here, exactly as you wrote it.',
    ru: 'Первая запись может быть одним предложением. Она останется здесь точно такой, как ты написал.',
  },
  'journal.write.eyebrow': { en: 'A moment for you', ru: 'Момент для тебя' },
  'journal.timeline.empty.filtered': {
    en: 'No entries match — try clearing the filters.',
    ru: 'Ничего не найдено — попробуй сбросить фильтры.',
  },
  'journal.timeline.empty.month': {
    en: 'No entries yet this month.',
    ru: 'В этом месяце записей пока нет.',
  },
  'journal.tag.remove.aria': { en: 'Remove tag {t}', ru: 'Убрать метку {t}' },
  // --- OD journal.html port: two-column shell, side-search, page head, feed ---
  'journal.search.title': { en: 'Search', ru: 'Поиск' },
  'journal.search.toggle': { en: 'Search entries', ru: 'Поиск по записям' },
  'journal.stats.entries': { en: '{n} entries', ru: '{n} записей' },
  'journal.stats.month': { en: 'this month {n}', ru: 'в этом месяце: {n}' },
  'journal.section.entries': { en: 'Entries', ru: 'Записи' },
  'journal.section.hint': { en: 'chronological', ru: 'хронология' },
  'journal.entry.expand': { en: 'Expand', ru: 'Раскрыть' },
  'journal.author.note': {
    en: 'Mood and how much it matters are yours — the companion does not guess them.',
    ru: 'Настроение и важность отмечаете вы — компаньон их не угадывает.',
  },

  'rt.title': { en: 'Routing & budget', ru: 'Маршруты и бюджет' },
  'rt.sub': { en: 'under the hood', ru: 'что внутри' },
  'rt.fallback.title': {
    en: 'If one model is busy, we use the next',
    ru: 'Если одна модель занята, идём к следующей',
  },
  'rt.fallback.desc': {
    en: 'Your message automatically moves to the next provider if one is busy or over budget — you never have to do anything.',
    ru: 'Если провайдер занят или превышен бюджет, сообщение автоматически уйдёт к следующему — ничего делать не нужно.',
  },
  'rt.h.provider': { en: 'Provider', ru: 'Провайдер' },
  'rt.h.model': { en: 'Model', ru: 'Модель' },
  'rt.h.status': { en: 'Status', ru: 'Статус' },
  'rt.h.req': { en: 'Requests', ru: 'Запросы' },
  'rt.h.cost': { en: 'Cost', ru: 'Расход' },
  'rt.h.speed': { en: 'Speed', ru: 'Скорость' },
  'rt.s.healthy': { en: 'healthy', ru: 'работает' },
  'rt.s.ratelimit': { en: 'rate-limited', ru: 'лимит запросов' },
  'rt.s.standby': { en: 'standby', ru: 'в резерве' },
  'rt.s.unavailable': { en: 'not configured', ru: 'не настроен' },
  'rt.s.hardstop': { en: 'cap reached', ru: 'лимит исчерпан' },
  'rt.budget.title': { en: 'Monthly budget', ru: 'Месячный бюджет' },
  'rt.budget.help': {
    en: 'soft-warn at 80% · hard-stop at 100%',
    ru: 'предупреждение на 80% · стоп на 100%',
  },
  'rt.budget.warn': { en: 'approaching cap', ru: 'близко к лимиту' },
  'rt.budget.stop': {
    en: 'cap reached — serving locally',
    ru: 'лимит исчерпан — отвечает локально',
  },
  'rt.fb': { en: 'Last fallback', ru: 'Последнее переключение' },
  'rt.fb.none': { en: 'none this session', ru: 'в этой сессии не было' },
  'rt.configure': { en: 'Configure keys →', ru: 'Настроить ключи →' },
  'rt.configure.hint': {
    en: 'Click a provider row to connect a key in Onboarding.',
    ru: 'Кликните по строке провайдера, чтобы подключить ключ в «Начале работы».',
  },
  'rt.langfuse': { en: 'Open traces in Langfuse', ru: 'Открыть трассировки в Langfuse' },
  'rt.empty': { en: 'No usage yet this month.', ru: 'В этом месяце запросов ещё не было.' },
  'rt.err': {
    en: 'Could not load routing state.',
    ru: 'Не удалось загрузить состояние маршрутизации.',
  },
  'rt.week.title': { en: 'This month', ru: 'В этом месяце' },
  'rt.week.desc': {
    en: 'Tokens & cost across all providers.',
    ru: 'Токены и расходы по всем провайдерам.',
  },
  'rt.tin': { en: 'Tokens in', ru: 'Входящие токены' },
  'rt.tout': { en: 'Tokens out', ru: 'Исходящие токены' },
  'rt.cost': { en: 'Total cost', ru: 'Всего расходов' },
  'rt.speed': { en: 'Avg speed', ru: 'Средняя скорость' },
  // --- OD routing.html port: 4-panel engineering surface ---
  'rt.lede': {
    en: 'Where turns are served. Configuration, not live probing.',
    ru: 'Где ходы обслуживаются. Конфигурация, не живой пинг.',
  },
  'rt.configchip': { en: 'config, not probing', ru: 'конфигурация, не пинг' },
  'rt.chain.title': { en: 'Fallback chain', ru: 'Цепочка fallback' },
  'rt.chain.local': { en: 'Local fallback', ru: 'Локальный резерв' },
  'rt.chain.sub': {
    en: 'ordered · local fallback last',
    ru: 'по порядку · локальный резерв в конце',
  },
  'rt.chain.note': {
    en: 'BYOK wins even with env keys present; the matching env candidate is skipped (skip-self). On 429/5xx/timeout it falls through silently. Budget is checked first.',
    ru: 'BYOK выигрывает даже при наличии env-ключей; подходящий по типу env-кандидат пропускается (skip-self). На 429/5xx/timeout — тихий переход. Бюджет проверяется первым.',
  },
  'rt.chain.perturn': { en: 'per turn', ru: 'по ходу' },
  'rt.budget.sub': { en: 'soft 80% · hard 100%', ru: 'soft 80% · hard 100%' },
  'rt.budget.oflimit': {
    en: 'of {limit} limit · {rem} remaining',
    ru: 'из лимита {limit} · осталось {rem}',
  },
  'rt.budget.flag.warn': { en: 'soft-warn ≥80%', ru: 'soft-warn ≥80%' },
  'rt.budget.flag.stop': { en: 'hard-stop ≥100%', ru: 'hard-stop ≥100%' },
  'rt.budget.note': {
    en: 'soft-warn ≥80% — turn proceeds. hard-stop ≥100% — real providers are skipped, the local fallback serves the turn.',
    ru: 'soft-warn ≥80% — ход идёт. hard-stop ≥100% — реальные провайдеры пропускаются, ход обслуживает локальный резерв.',
  },
  'rt.providers.title': { en: 'Providers', ru: 'Провайдеры' },
  'rt.providers.sub': { en: 'config-derived health', ru: 'здоровье из конфига' },
  'rt.lastfb.title': { en: 'Last fallback', ru: 'Последний fallback' },
  'rt.lastfb.sub': { en: 'process-local', ru: 'process-local' },
  'rt.lastfb.lost': { en: 'lost on restart', ru: 'теряется на рестарте' },
  'rt.footnote': {
    en: 'health is configuration-derived, not live probing · configured=healthy · ollama-without-url=standby · removed-with-prior-usage=unavailable',
    ru: 'здоровье — конфигурация, не живой пинг · configured=healthy · ollama-without-url=standby · removed-with-prior-usage=unavailable',
  },

  'ps.title': { en: 'Your companions', ru: 'Ваши собеседники' },
  'ps.ready': { en: 'ready', ru: 'готовы' },
  'ps.gallery.desc': {
    en: 'Five companions, each with a distinct way of being with you. Tap “Chat with…” to start a conversation, or use one as a starting point to customize.',
    ru: 'Пять собеседников, у каждого свой способ быть рядом. Нажмите «Чат с…», чтобы начать разговор, или возьмите любого за основу для настройки.',
  },
  'ps.new.title': { en: 'New companion', ru: 'Новый собеседник' },
  'ps.new.desc': {
    en: 'Describe how they should be with you. The preview updates live.',
    ru: 'Опишите, каким он должен быть рядом с вами. Превью обновляется на лету.',
  },
  'ps.startfrom': { en: 'Start from a companion', ru: 'Начать с собеседника' },
  'ps.blank': { en: 'Blank', ru: 'С нуля' },
  'ps.name': { en: 'Name', ru: 'Имя' },
  'ps.role': { en: 'Role / vibe', ru: 'Роль и манера' },
  'ps.tone': { en: 'Tone', ru: 'Тон' },
  'ps.warmth': { en: 'Warmth', ru: 'Теплота' },
  'ps.direct': { en: 'Directness', ru: 'Прямота' },
  'ps.pace': { en: 'Pace', ru: 'Темп' },
  // Tooltips (hover the slider label) — one line each, plain language.
  'ps.warmth.tip': {
    en: 'How warm and affirming the voice is. High = leads with validation; low = measured and neutral.',
    ru: 'Насколько голос тёплый и поддерживающий. Выше — сначала подтверждение; ниже — сдержаннее и нейтральнее.',
  },
  'ps.direct.tip': {
    en: 'How direct the companion is. High = names the pattern and proposes a concrete next step; low = open questions and reflection.',
    ru: 'Насколько прям собеседник. Выше — называет закономерность и предлагает конкретный шаг; ниже — открытые вопросы и рефлексия.',
  },
  'ps.pace.tip': {
    en: 'Rhythm of replies. High = tight and brief, moves forward; low = slower, leaves pauses, does not rush to resolve.',
    ru: 'Ритм ответов. Выше — короче и плотнее, движется вперёд; ниже — медленнее, с паузами, не спешит к развязке.',
  },
  'ps.prompt': { en: 'System prompt', ru: 'Системный промпт' },
  // Structured persona prompts: specialization / character / approach.
  'ps.presets': { en: 'Prompt presets', ru: 'Готовые наборы промптов' },
  'ps.presets.help': {
    en: 'Apply a starting set of prompts — then edit any field. Only the three prompt fields change.',
    ru: 'Подставьте начальный набор промптов — потом отредактируйте любое поле. Меняются только три поля промптов.',
  },
  'ps.spec': { en: 'Specialization', ru: 'Специализация' },
  'ps.spec.ph': {
    en: 'e.g. a calm reflective therapist',
    ru: 'напр., спокойный рефлексивный терапевт',
  },
  'ps.spec.help': {
    en: 'Who they are — their role or specialty (therapist, friend, coach, mentor…).',
    ru: 'Кто они — их роль или специализация (терапевт, друг, коуч, наставник…).',
  },
  'ps.character': { en: 'Character', ru: 'Характер' },
  'ps.character.ph': {
    en: 'e.g. warm, calm, listens closely',
    ru: 'напр., тёплый, спокойный, внимательно слушает',
  },
  'ps.character.help': {
    en: 'Personality and manner — how they come across, beyond their role.',
    ru: 'Характер и манера — какими они ощущаются, помимо роли.',
  },
  'ps.approach': { en: 'Approach', ru: 'Подход' },
  'ps.approach.ph': {
    en: 'e.g. name the emotion before offering a frame; keep replies short',
    ru: 'напр., сначала назовите эмоцию, потом предложите рамку; ответы держите короткими',
  },
  'ps.approach.help': {
    en: 'How they work — the method or rules that shape each reply.',
    ru: 'Как они работают — метод или правила, формирующие каждый ответ.',
  },
  'ps.open': { en: 'Opening line', ru: 'Приветствие' },
  // Section headings that break the long create form into scannable groups.
  'ps.sec.identity': { en: 'Identity', ru: 'Личность' },
  'ps.sec.voice': { en: 'Voice', ru: 'Голос' },
  'ps.sec.prompt': { en: 'Prompt', ru: 'Промпт' },
  'ps.sec.opening': { en: 'Opening', ru: 'Начало' },
  'ps.save': { en: 'Save companion', ru: 'Сохранить собеседника' },
  'ps.test': { en: 'Test in chat →', ru: 'Проверить в чате →' },
  'ps.preview': { en: 'Preview', ru: 'Превью' },
  'ps.preview.how': { en: 'How', ru: 'Как' },
  'ps.preview.opens': { en: 'opens.', ru: 'начинает разговор.' },
  'ps.chip1': { en: 'disclose, don’t perform', ru: 'искренность, а не игра' },
  'ps.chip2': { en: 'no exclamation marks', ru: 'без восклицательных знаков' },
  'ps.help': {
    en: 'Tone sliders tune memory salience and persona phrasing together — so memory and empathy stay aligned (the gap that plain RAG gets wrong).',
    ru: 'Слайдеры тона настраивают значимость памяти и формулировки вместе — чтобы память и эмпатия оставались согласованными (то, что обычный RAG как раз упускает).',
  },
  'ps.warmth.lbl': { en: 'warmth', ru: 'теплота' },
  'ps.chatwith': { en: 'Chat with', ru: 'Чат с' },
  'ps.yours': { en: 'of yours', ru: 'ваших' },
  // Avatar palette + slider endpoint labels (semantics of low/high on each
  // tone axis), composed-prompt disclosure, edit/delete affordances.
  'ps.palette': { en: 'Avatar', ru: 'Аватар' },
  'ps.palette.help': {
    en: 'Pick a colour identity. It shows on the card and in chat.',
    ru: 'Выберите цветовой образ. Он будет на карточке и в чате.',
  },
  'ps.warmth.lo': { en: 'reserved', ru: 'сдержанно' },
  'ps.warmth.hi': { en: 'warm', ru: 'тепло' },
  'ps.direct.lo': { en: 'reflective', ru: 'рефлексивно' },
  'ps.direct.hi': { en: 'direct', ru: 'прямо' },
  'ps.pace.lo': { en: 'unhurried', ru: 'размеренно' },
  'ps.pace.hi': { en: 'brisk', ru: 'живо' },
  'ps.compose': { en: 'What the companion hears', ru: 'Что услышит компаньон' },
  'ps.compose.note': {
    en: 'Sent each turn as the system prompt. The safety line is added automatically and cannot be removed.',
    ru: 'Отправляется каждый ход как системный промпт. Строка безопасности добавляется автоматически и её нельзя убрать.',
  },
  'ps.update': { en: 'Update companion', ru: 'Обновить собеседника' },
  'ps.editing.title': { en: 'Edit companion', ru: 'Редактировать собеседника' },
  'ps.editing.desc': {
    en: 'Tweak any field. The preview updates live.',
    ru: 'Поправьте любое поле. Превью обновляется на лету.',
  },
  'ps.edit': { en: 'Edit', ru: 'Изменить' },
  'ps.delete': { en: 'Delete', ru: 'Удалить' },
  'ps.active': { en: 'Active', ru: 'Активен' },
  'ps.select': { en: 'Select', ru: 'Выбрать' },
  'ps.chat': { en: 'Chat', ru: 'Чат' },
  'ps.delete.confirm': {
    en: 'Delete this companion? Conversations already started with it stay.',
    ru: 'Удалить этого собеседника? Начатые с ним разговоры останутся.',
  },
  // OD personas page shell: pagehead lede + badge, the two section titles, and
  // the honest-limit footer (the "disclose, don't perform" invariant, verbatim
  // from the OD design — the companion is a voice, not a person).
  'ps.lede': {
    en: 'Five ready-made companions or your own. Tone, warmth, pace.',
    ru: 'Пять готовых собеседников или свой. Тон, теплота, темп.',
  },
  'ps.badge.five': { en: 'five ready or your own', ru: 'пять готовых или свой' },
  'ps.section.ready': { en: 'Ready-made companions', ru: 'Готовые собеседники' },
  'ps.section.own': { en: 'Your own companion', ru: 'Свой компаньон' },
  'ps.limitline': {
    en: 'The companion is a voice, not a person. It doesn’t perform feelings.',
    ru: 'Компаньон — голос, не человек. Чувств не играет.',
  },

  // --- Practices: breathing + meditation (Opera-Air-style wellness menu) ---
  'pr.title': { en: 'Practices', ru: 'Практики' },
  'pr.subtitle': {
    en: 'Small tools to settle, between turns',
    ru: 'Небольшие практики, чтобы выдохнуть между репликами',
  },
  'pr.tab.breathing': { en: 'Breathing', ru: 'Дыхание' },
  'pr.tab.meditation': { en: 'Meditation', ru: 'Медитация' },
  'pr.start': { en: 'Begin', ru: 'Начать' },
  'pr.stop': { en: 'Stop', ru: 'Остановить' },
  'pr.again': { en: 'Begin again', ru: 'Начать заново' },
  'pr.cycles': { en: 'Cycles: {n}', ru: 'Циклов: {n}' },
  // Breathing
  'pr.breathing.intro': {
    en: 'Follow the circle. Inhale as it grows, exhale as it shrinks.',
    ru: 'Следите за кругом. Вдох — когда он растёт, выдох — когда сжимается.',
  },
  'pr.pattern.box': { en: 'Box', ru: 'Квадрат' },
  'pr.pattern.box.desc': {
    en: 'Inhale 4 · Hold 4 · Exhale 4 · Hold 4 — steady and balancing.',
    ru: 'Вдох 4 · Задержка 4 · Выдох 4 · Задержка 4 — ровно, выравнивает.',
  },
  'pr.pattern.calm': { en: 'Calm', ru: 'Спокойствие' },
  'pr.pattern.calm.desc': {
    en: 'Inhale 4 · Hold 4 · Exhale 6 · Hold 2 — eases tension.',
    ru: 'Вдох 4 · Задержка 4 · Выдох 6 · Задержка 2 — снимает напряжение.',
  },
  'pr.pattern.relax': { en: '4-7-8 Relax', ru: '4-7-8 Расслабление' },
  'pr.pattern.relax.desc': {
    en: 'Inhale 4 · Hold 7 · Exhale 8 — for sleep and deep calm.',
    ru: 'Вдох 4 · Задержка 7 · Выдох 8 — для сна и глубокого спокойствия.',
  },
  'pr.phase.inhale': { en: 'Inhale', ru: 'Вдох' },
  'pr.phase.hold': { en: 'Hold', ru: 'Задержка' },
  'pr.phase.exhale': { en: 'Exhale', ru: 'Выдох' },
  // Meditation
  'pr.meditation.intro': {
    en: 'A timed session with a soft bell at the start and end.',
    ru: 'Сеанс по таймеру с мягким колокольчиком в начале и в конце.',
  },
  'pr.duration': { en: 'Duration', ru: 'Длительность' },
  'pr.minutes': { en: '{n} min', ru: '{n} мин' },
  'pr.theme': { en: 'Theme', ru: 'Тема' },
  'pr.theme.breath': { en: 'Breath awareness', ru: 'Осознанность дыхания' },
  'pr.theme.breath.desc': {
    en: 'Rest attention on the breath; return when it wanders.',
    ru: 'Держите внимание на дыхании; возвращайтесь, когда оно уходит.',
  },
  'pr.theme.breath.cue': {
    en: 'Settle in. Let the breath come and go at its own pace. When the mind wanders, return to the feeling of the breath.',
    ru: 'Устройтесь поудобнее. Пусть дыхание идёт своим ритмом. Когда ум уходит — возвращайтесь к ощущению дыхания.',
  },
  'pr.theme.body': { en: 'Body scan', ru: 'Сканирование тела' },
  'pr.theme.body.desc': {
    en: 'Move attention from head to feet; notice without changing.',
    ru: 'Переводите внимание от макушки к стопам; замечайте, не пытаясь изменить.',
  },
  'pr.theme.body.cue': {
    en: 'Move your attention slowly from the top of your head to your feet. Notice each part without changing anything.',
    ru: 'Медленно переводите внимание от макушки к стопам. Замечайте каждую часть, ничего не меняя.',
  },
  'pr.theme.metta': { en: 'Loving-kindness', ru: 'Доброта к себе и другим' },
  'pr.theme.metta.desc': {
    en: 'Silently wish ease for yourself, then for someone else.',
    ru: 'Мысленно пожелайте покоя себе, затем — другому человеку.',
  },
  'pr.theme.metta.cue': {
    en: 'Silently wish yourself ease, then wish it for someone else. May you be at ease.',
    ru: 'Мысленно пожелайте себе покоя, затем — другому. Пусть вам будет спокойно.',
  },
  'pr.remaining': { en: 'remaining', ru: 'осталось' },
  'pr.done': { en: 'complete', ru: 'готово' },
  'pr.pause': { en: 'Pause', ru: 'Пауза' },
  'pr.resume': { en: 'Resume', ru: 'Продолжить' },
  'pr.speak.cues': { en: 'Speak the intro', ru: 'Озвучить вступление' },
  // --- OD practices.html port: page head, session meta, honest limit line ---
  'pr.offline': { en: 'offline', ru: 'офлайн' },
  'pr.cycles.label': { en: 'cycles', ru: 'циклов' },
  'pr.min': { en: 'min', ru: 'мин' },
  'pr.limit': {
    en: 'Breathing and meditation are tools, not a replacement for therapy.',
    ru: 'Дыхание и медитация — инструменты, не замена терапии.',
  },

  'pl.title': { en: 'Your plan', ru: 'Тариф' },
  'pl.sub': {
    en: 'bring your keys — or let us handle it',
    ru: 'свои ключи — или доверьте всё нам',
  },
  'pl.h1': { en: 'Two ways to use Stillside.', ru: 'Два способа пользоваться Stillside.' },
  'pl.p': {
    en: 'It’s always open-source. Bring your own API keys for free, or subscribe and we handle the keys, routing, and infra — you just talk.',
    ru: 'Это всегда открытый код. Подключите свои API-ключи бесплатно или оформите подписку — и мы берём ключи, маршрутизацию и инфраструктуру на себя. Вы просто разговариваете.',
  },
  'pl.monthly': { en: 'Monthly', ru: 'Помесячно' },
  'pl.yearly': { en: 'Yearly · save 20%', ru: 'За год · экономия 20%' },
  'pl.note.b': {
    en: 'Your conversations stay private.',
    ru: 'Ваши диалоги остаются приватными.',
  },
  'pl.note.s': {
    en: 'On every plan, your messages are encrypted at rest. BYOK users: the server only ever sees an encrypted key handle. Subscribers: we process requests to route them, but never train on your conversations and never sell data. Cancel anytime — your memory export is yours to keep.',
    ru: 'На любом тарифе сообщения шифруются при хранении. Со своими ключами: сервер видит только зашифрованный дескриптор ключа. С подпиской: мы обрабатываем запросы, чтобы их маршрутизировать, но не обучаемся на ваших диалогах и не продаём данные. Отменить можно в любой момент — экспорт памяти остаётся у вас.',
  },
  'pl.free.cta': { en: 'You’re here', ru: 'Вы здесь' },
  'pl.plus.cta': { en: 'Choose Plus', ru: 'Выбрать Plus' },
  'pl.pro.cta': { en: 'Choose Pro', ru: 'Выбрать Pro' },
  'pl.badge': { en: 'Most loved', ru: 'Чаще всего выбирают' },
  'pl.billed.yearly': { en: '/mo billed yearly', ru: '/мес, оплата за год' },
  'pl.per.month': { en: '/month', ru: '/мес' },
  'pl.forever': { en: '/forever', ru: 'навсегда' },
  // Shown on /plans when the instance doesn't sell plans (self-hosted, billing
  // off). The page must not advertise a checkout the deployment can't serve.
  'pl.unavail.h1': {
    en: 'Plans are not available on this instance.',
    ru: 'На этом экземпляре тарифы недоступны.',
  },
  'pl.unavail.p': {
    en: 'This Stillside instance is self-hosted — it runs on your own API keys (BYOK) with no paid plans or checkout. Bring a key in onboarding to start.',
    ru: 'Этот экземпляр Stillside — self-hosted: он работает на ваших собственных API-ключах (BYOK), без платных тарифов и оплаты. Добавьте ключ в онбординге, чтобы начать.',
  },
  'pl.unavail.cta': { en: 'Back to home', ru: 'На главную' },

  'set.title': { en: 'Settings', ru: 'Настройки' },
  'set.sub': { en: 'device, keys, data', ru: 'устройство, ключи, данные' },
  'set.appearance': { en: 'Appearance', ru: 'Оформление' },
  'set.theme': { en: 'Theme', ru: 'Тема' },
  'set.theme.dark': { en: 'Dark', ru: 'Тёмная' },
  'set.theme.light': { en: 'Light', ru: 'Светлая' },
  'set.language': { en: 'Language', ru: 'Язык' },
  'set.vault': { en: 'Key vault', ru: 'Хранилище ключей' },
  'set.vault.status': {
    en: 'No keys connected yet.',
    ru: 'Ключей пока не подключено.',
  },
  'set.vault.hint': {
    en: 'Keys are sealed in transit (ECDH to the server session key) and stored envelope-encrypted on the server. The server holds the decryption key and can read your key at reply time — NOT zero-knowledge; protects against a database dump, not the server operator. Keys survive a browser-data wipe and work across your devices.',
    ru: 'Ключи запечатываются при передаче (ECDH на сессионный ключ сервера) и хранятся на сервере в конвертном шифровании. Сервер хранит ключ расшифровки и может прочитать ваш ключ при ответе — НЕ нулевое разглашение; защита от дампа БД, а не от оператора сервера. Ключи переживут очистку данных браузера и работают на всех ваших устройствах.',
  },
  // Multi-key BYOK (replaces the single-key empty state with a real action).
  'set.vault.add_key': { en: 'Add a key', ru: 'Добавить ключ' },
  'set.vault.add_another': { en: 'Add another key', ru: 'Добавить ещё ключ' },
  'set.vault.set_active': { en: 'Set as active', ru: 'Сделать активным' },
  'set.vault.active': { en: 'active', ru: 'активен' },
  // Modal / form chrome.
  'byok.get_api_key': { en: 'Get API key', ru: 'Получить ключ' },
  'byok.show_key': { en: 'Show', ru: 'Показать' },
  'byok.hide_key': { en: 'Hide', ru: 'Скрыть' },
  'byok.endpoint': { en: 'Endpoint URL', ru: 'URL эндпоинта' },
  'byok.endpoint.help': {
    en: 'Leave blank to use the provider default. For Azure: paste the resource endpoint from the Azure portal.',
    ru: 'Пусто — адрес по умолчанию. Для Azure: вставьте адрес ресурса из портала Azure.',
  },
  'byok.label.help': {
    en: 'A friendly name so you can tell your keys apart in the vault list.',
    ru: 'Понятное имя, чтобы различать ключи в списке хранилища.',
  },
  'byok.required': { en: 'Required', ru: 'Обязательно' },
  'byok.aws.access_key': { en: 'AWS Access Key ID', ru: 'AWS Access Key ID' },
  'byok.aws.secret_key': { en: 'AWS Secret Access Key', ru: 'AWS Secret Access Key' },
  'byok.aws.region': { en: 'AWS Region', ru: 'AWS-регион' },
  'byok.aws.help': {
    en: 'All three are required for Bedrock. Find them in the AWS Console → IAM → Users → Security credentials.',
    ru: 'Для Bedrock нужны все три. Найдите их в AWS Console → IAM → Users → Security credentials.',
  },
  'byok.cancel': { en: 'Cancel', ru: 'Отмена' },
  'byok.add': { en: 'Add key', ru: 'Добавить ключ' },
  'byok.saving': { en: 'Saving…', ru: 'Сохраняем…' },
  // Family-key tab.
  'fam.key.add_key': { en: 'Add a family key', ru: 'Добавить ключ семьи' },
  'fam.key.add_another': { en: 'Add another family key', ru: 'Добавить ещё ключ семьи' },
  // Owner-only "use my personal key" toggle. When on, the family rides the
  // owner's active personal BYOK key instead of a separate family key — no
  // second key entry. Mutually exclusive with family keys (UI hides the
  // family key form when on).
  'fam.key.use_personal.label': {
    en: 'Use my personal key',
    ru: 'Использовать мой личный ключ',
  },
  'fam.key.use_personal.help': {
    en: 'Your active personal LLM key serves every family member’s chat. It is decrypted in memory only for each reply and zeroized after — members never see the key. Mutually exclusive with family keys.',
    ru: 'Ваш активный личный ключ LLM обслуживает чат всех членов семьи. Он расшифровывается в памяти только для ответа и затем обнуляется — члены семьи никогда не видят ключ. Взаимоисключающе с семейными ключами.',
  },
  'fam.key.use_personal.no_personal_key': {
    en: 'You have no personal LLM key yet. Add one in /onboarding first.',
    ru: 'У вас ещё нет личного ключа LLM. Сначала добавьте его в /onboarding.',
  },
  'fam.key.use_personal.in_use': {
    en: 'Using your personal key:',
    ru: 'Используется ваш личный ключ:',
  },
  'fam.key.use_personal.nonowner_notice_on': {
    en: 'The family uses the owner’s personal LLM key. Ask the owner if you need a different model.',
    ru: 'Семья использует личный ключ LLM владельца. Обратитесь к владельцу, если нужна другая модель.',
  },
  'set.data': { en: 'Your data', ru: 'Ваши данные' },
  'set.data.hint': {
    en: 'Memory and conversations live on your device and your self-hosted Postgres. Export or wipe anytime.',
    ru: 'Память и диалоги хранятся на устройстве и в вашем собственном Postgres. Экспорт или удаление — в любой момент.',
  },
};

type Ctx = {
  lang: Lang;
  setLang: (l: Lang) => void;
  toggleLang: () => void;
  // `vars` (optional) replaces `{name}` placeholders in the localized string —
  // used for value-bearing copy like "drawn from {n} turns" / "updated {when}".
  t: (k: string, vars?: Record<string, string | number>) => string;
  L2: (o: Localized) => string;
};

const LangContext = createContext<Ctx | null>(null);

const STORAGE_KEY = 'companion.lang';

export function LangProvider({ children }: { children: ReactNode }) {
  // The pre-hydration boot script in ``app/layout.tsx`` has already set
  // ``<html lang>`` to the saved value (or left the default 'en'). We
  // read it back here so the first React render is consistent with the
  // markup the user actually saw — no en→ru flash.
  const [lang, setLangState] = useState<Lang>(() => {
    if (typeof document === 'undefined') return 'en';
    const v = document.documentElement.lang;
    return v === 'ru' ? 'ru' : 'en';
  });

  useEffect(() => {
    if (typeof document !== 'undefined') document.documentElement.lang = lang;
    if (typeof window !== 'undefined') localStorage.setItem(STORAGE_KEY, lang);
  }, [lang]);

  const value = useMemo<Ctx>(
    () => ({
      lang,
      setLang: setLangState,
      toggleLang: () => setLangState((p) => (p === 'en' ? 'ru' : 'en')),
      t: (k: string, vars?: Record<string, string | number>) => {
        const s = I18N[k]?.[lang] ?? I18N[k]?.en ?? k;
        if (!vars) return s;
        return s.replace(/\{(\w+)\}/g, (_, name) =>
          name in vars ? String(vars[name]) : `{${name}}`,
        );
      },
      L2: (o: Localized) => o[lang] ?? o.en,
    }),
    [lang],
  );

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): Ctx {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error('useLang must be used within LangProvider');
  return ctx;
}

export function useT() {
  return useLang().t;
}
