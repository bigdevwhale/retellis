// K6: the conversation list hydrates from the server (GET /v1/conversations)
// and survives a refresh. Covers: replace fixtures with server data, empty
// list seeds a fresh chat, lazy message load per convo, and the offline
// fallback (fetch rejects → fixtures stay, hydrated stays false so a retry
// can still succeed). We mock the api-client wrappers directly.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api-client';
import { useStore } from '../lib/store';

vi.mock('../lib/api-client', async (importOriginal) => {
  const real = await importOriginal<typeof api>();
  return {
    ...real,
    listConversations: vi.fn(),
    listEvents: vi.fn(),
  };
});

const listConversations = api.listConversations as unknown as ReturnType<typeof vi.fn>;
const listEvents = api.listEvents as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  // Reset the convo slice to the seeded fixtures default so each case starts
  // from the same state, and clear the hydrated latch.
  useStore.setState({
    convos: [],
    activeConvoId: '',
    activePersonaId: 'aria',
    hydrated: false,
  });
  // Re-seed a fresh fixture-like state by calling newChat (gives one local
  // convo with an opening message).
  useStore.getState().newChat('aria');
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('hydrateConvos', () => {
  it('replaces the local list with server conversations and sets hydrated', async () => {
    // Server returns last_activity desc (c-hello is the most recent).
    listConversations.mockResolvedValueOnce([
      {
        convo_id: 'c-hello',
        persona_id: 'sam',
        title: 'quick hello',
        preview: 'hi',
        event_count: 1,
        created_at: '2026-07-14T08:00:00Z',
        last_activity: '2026-07-14T08:00:00Z',
        family_id: null,
        visibility: 'private',
      },
      {
        convo_id: 'c-week',
        persona_id: 'aria',
        title: 'A heavy week at work',
        preview: 'Work’s been heavy…',
        event_count: 2,
        created_at: '2026-07-13T09:42:00Z',
        last_activity: '2026-07-13T09:42:00Z',
        family_id: null,
        visibility: 'private',
      },
    ]);
    await useStore.getState().hydrateConvos();
    const s = useStore.getState();
    expect(s.hydrated).toBe(true);
    expect(s.convos.map((c) => c.id)).toEqual(['c-hello', 'c-week']);
    // Most-recent (c-hello, first) becomes active when the previous active is
    // gone (the local fixture id isn't on the server).
    expect(s.activeConvoId).toBe('c-hello');
    // Server title/preview map into the Localized shape; msgs load lazily.
    expect(s.convos[0]!.title.en).toBe('quick hello');
    expect(s.convos[0]!.msgs).toEqual([]);
  });

  it('seeds a fresh chat when the server list is empty', async () => {
    listConversations.mockResolvedValueOnce([]);
    await useStore.getState().hydrateConvos();
    const s = useStore.getState();
    expect(s.hydrated).toBe(true);
    expect(s.convos).toHaveLength(1);
    // The fresh chat has an opening message from the persona.
    expect(s.convos[0]!.msgs.length).toBeGreaterThan(0);
  });

  it('keeps the fixtures and leaves hydrated=false when the API fails (offline fallback)', async () => {
    const before = useStore.getState().convos;
    listConversations.mockRejectedValueOnce(new Error('network down'));
    await useStore.getState().hydrateConvos();
    const s = useStore.getState();
    expect(s.hydrated).toBe(false);
    // List unchanged so the UI still renders against the local state offline.
    expect(s.convos).toBe(before);
  });

  it('is idempotent — a second call no-ops once hydrated', async () => {
    listConversations.mockResolvedValueOnce([
      {
        convo_id: 'c1',
        persona_id: 'aria',
        title: 't',
        preview: 'p',
        event_count: 1,
        created_at: '2026-07-13T09:00:00Z',
        last_activity: '2026-07-13T09:00:00Z',
        family_id: null,
        visibility: 'private',
      },
    ]);
    await useStore.getState().hydrateConvos();
    expect(listConversations).toHaveBeenCalledTimes(1);
    // Second call must not fetch again.
    await useStore.getState().hydrateConvos();
    expect(listConversations).toHaveBeenCalledTimes(1);
  });
});

describe('loadConvoMessages', () => {
  it('populates msgs from server events and maps role→them/content→t', async () => {
    listConversations.mockResolvedValueOnce([
      {
        convo_id: 'c-week',
        persona_id: 'aria',
        title: 'A heavy week',
        preview: 'Work’s been heavy…',
        event_count: 2,
        created_at: '2026-07-13T09:42:00Z',
        last_activity: '2026-07-13T09:42:00Z',
        family_id: null,
        visibility: 'private',
      },
    ]);
    await useStore.getState().hydrateConvos();
    expect(useStore.getState().convos[0]!.msgs).toEqual([]);

    listEvents.mockResolvedValueOnce([
      {
        id: 'e1',
        user_id: 'u',
        persona_id: 'aria',
        prev_event_id: null,
        role: 'user',
        content: 'I had a heavy week.',
        salience: 0,
        emotion_tags: [],
      },
      {
        id: 'e2',
        user_id: 'u',
        persona_id: 'aria',
        prev_event_id: 'e1',
        role: 'assistant',
        content: 'That sounds hard.',
        salience: 0,
        emotion_tags: [],
      },
    ]);
    await useStore.getState().loadConvoMessages('c-week');
    const convo = useStore.getState().convos.find((c) => c.id === 'c-week')!;
    expect(convo.msgs.map((m) => m.them)).toEqual([false, true]);
    expect(convo.msgs.map((m) => m.t.en)).toEqual(['I had a heavy week.', 'That sounds hard.']);
  });

  it('carries participant_user_id into speakerUserId (None on assistant events)', async () => {
    // The joint-thread renderer attributes each bubble to its author via
    // ``Message.speakerUserId``, which ``eventToMessage`` copies from the
    // server event's ``participant_user_id``. User events carry the speaker;
    // assistant events carry None (the therapist speaks as one voice).
    listConversations.mockResolvedValueOnce([
      {
        convo_id: 'fam-joint-fam-1',
        persona_id: 'fam',
        title: 'Joint',
        preview: 'p',
        event_count: 2,
        created_at: '2026-07-24T00:00:00Z',
        last_activity: '2026-07-24T00:00:00Z',
        family_id: 'fam-1',
        visibility: 'shared',
      },
    ]);
    await useStore.getState().hydrateConvos();
    expect(useStore.getState().convos[0]!.msgs).toEqual([]);

    listEvents.mockResolvedValueOnce([
      {
        id: 'e1',
        user_id: 'u-other',
        persona_id: 'fam',
        prev_event_id: null,
        role: 'user',
        content: 'I feel unheard.',
        salience: 0,
        emotion_tags: [],
        participant_user_id: 'u-other',
      },
      {
        id: 'e2',
        user_id: 'u-other',
        persona_id: 'fam',
        prev_event_id: 'e1',
        role: 'assistant',
        content: 'Tell me more.',
        salience: 0,
        emotion_tags: [],
        participant_user_id: null,
      },
    ]);
    await useStore.getState().loadConvoMessages('fam-joint-fam-1');
    const convo = useStore.getState().convos.find((c) => c.id === 'fam-joint-fam-1')!;
    // User event carries the speaker id; assistant event carries none.
    expect(convo.msgs.map((m) => m.speakerUserId)).toEqual(['u-other', undefined]);
  });

  it('skips convos that already have messages (no double fetch)', async () => {
    await useStore.getState().loadConvoMessages(useStore.getState().activeConvoId);
    // The freshly-seeded local convo already has an opening message, so the
    // server is not contacted.
    expect(listEvents).not.toHaveBeenCalled();
  });

  it('does not throw into the UI when the history fetch fails', async () => {
    listConversations.mockResolvedValueOnce([
      {
        convo_id: 'c-x',
        persona_id: 'aria',
        title: 't',
        preview: 'p',
        event_count: 1,
        created_at: '2026-07-13T09:00:00Z',
        last_activity: '2026-07-13T09:00:00Z',
        family_id: null,
        visibility: 'private',
      },
    ]);
    await useStore.getState().hydrateConvos();
    listEvents.mockRejectedValueOnce(new Error('boom'));
    await expect(useStore.getState().loadConvoMessages('c-x')).resolves.toBeUndefined();
    // Thread stays empty (the user can still send a new message).
    expect(useStore.getState().convos.find((c) => c.id === 'c-x')!.msgs).toEqual([]);
  });

  it('passes the family SHARED filter for a fam-joint- convo (joint visibility)', async () => {
    // Joint session: loadConvoMessages must request the family shared scope so
    // the server returns every member's shared messages, not just the
    // requester's own. family + myUserId must be set on the store.
    useStore.setState({
      family: {
        id: 'fam-1',
        name: 'Test',
        owner_user_id: 'u-owner',
        created_at: '2026-07-09T00:00:00Z',
        family_salt: 'AAA=',
        family_enc_blob_seed: null,
        use_owner_personal_key: false,
      },
      myUserId: 'u-me',
      convos: [
        {
          id: 'fam-joint-fam-1',
          personaId: 'fam',
          title: { en: 'x', ru: 'x' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: 'x', ru: 'x' },
          msgs: [],
        },
      ],
      activeConvoId: 'fam-joint-fam-1',
      activePersonaId: 'fam',
    });
    listEvents.mockResolvedValueOnce([]);
    await useStore.getState().loadConvoMessages('fam-joint-fam-1');
    expect(listEvents).toHaveBeenCalledWith('fam', 200, {
      convoId: 'fam-joint-fam-1',
      familyFilter: { familyId: 'fam-1', visibility: 'shared', participantUserId: 'u-me' },
    });
  });

  it('omits the family filter for a fam-solo- convo (solo stays per-member)', async () => {
    useStore.setState({
      family: {
        id: 'fam-1',
        name: 'Test',
        owner_user_id: 'u-owner',
        created_at: '2026-07-09T00:00:00Z',
        family_salt: 'AAA=',
        family_enc_blob_seed: null,
        use_owner_personal_key: false,
      },
      myUserId: 'u-me',
      convos: [
        {
          id: 'fam-solo-me-x',
          personaId: 'fam',
          title: { en: 'x', ru: 'x' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: 'x', ru: 'x' },
          msgs: [],
        },
      ],
      activeConvoId: 'fam-solo-me-x',
      activePersonaId: 'fam',
    });
    listEvents.mockResolvedValueOnce([]);
    await useStore.getState().loadConvoMessages('fam-solo-me-x');
    const opts = listEvents.mock.calls[listEvents.mock.calls.length - 1]![2] as {
      familyFilter?: unknown;
    };
    expect(opts.familyFilter).toBeUndefined();
  });

  it('omits the family filter for a non-fam convo', async () => {
    useStore.setState({
      family: null,
      myUserId: null,
      convos: [
        {
          id: 'c-aria',
          personaId: 'aria',
          title: { en: 'x', ru: 'x' },
          ts: { en: 'now', ru: 'сейчас' },
          preview: { en: 'x', ru: 'x' },
          msgs: [],
        },
      ],
      activeConvoId: 'c-aria',
      activePersonaId: 'aria',
    });
    listEvents.mockResolvedValueOnce([]);
    await useStore.getState().loadConvoMessages('c-aria');
    const opts = listEvents.mock.calls[listEvents.mock.calls.length - 1]![2] as {
      familyFilter?: unknown;
    };
    expect(opts.familyFilter).toBeUndefined();
  });
});

describe('loadConvoMessages force refresh (joint family thread)', () => {
  // The joint thread is written by EVERY family member, but the client only
  // learns about new messages by fetching — the load-once guard alone meant
  // a member never saw what others sent after their first load. The joint
  // refresh (activate/focus/poll in ChatScreen) calls with { force: true }.
  const FAMILY = {
    id: 'fam-1',
    name: 'Test',
    owner_user_id: 'u-owner',
    created_at: '2026-07-09T00:00:00Z',
    family_salt: 'AAA=',
    family_enc_blob_seed: null,
    use_owner_personal_key: false,
  };
  const JOINT_CONVO = {
    id: 'fam-joint-fam-1',
    personaId: 'fam',
    title: { en: 'Joint', ru: 'Совместно' },
    ts: { en: 'now', ru: 'сейчас' },
    preview: { en: 'x', ru: 'x' },
    msgs: [
      // The member's own previously loaded message — the load-once guard
      // would normally make any refetch a no-op from here on.
      { them: false, t: { en: 'my earlier message', ru: 'my earlier message' }, ts: 'now' },
    ],
  };

  beforeEach(() => {
    useStore.setState({
      family: FAMILY,
      myUserId: 'u-me',
      convos: [JOINT_CONVO],
      activeConvoId: JOINT_CONVO.id,
      activePersonaId: 'fam',
    });
  });

  it('force bypasses the load-once guard and picks up another member’s new message', async () => {
    // Sanity: without force the convo already has messages → no fetch.
    await useStore.getState().loadConvoMessages(JOINT_CONVO.id);
    expect(listEvents).not.toHaveBeenCalled();

    // The other member's message arrived on the server after our first load.
    listEvents.mockResolvedValueOnce([
      {
        id: 'e1',
        user_id: 'u-me',
        persona_id: 'fam',
        prev_event_id: null,
        role: 'user',
        content: 'my earlier message',
        salience: 0,
        emotion_tags: [],
        participant_user_id: 'u-me',
      },
      {
        id: 'e2',
        user_id: 'u-other',
        persona_id: 'fam',
        prev_event_id: 'e1',
        role: 'user',
        content: 'can anyone hear me?',
        salience: 0,
        emotion_tags: [],
        participant_user_id: 'u-other',
      },
    ]);
    await useStore.getState().loadConvoMessages(JOINT_CONVO.id, { force: true });
    // Fetched with the family SHARED filter (not the personal scope).
    expect(listEvents).toHaveBeenCalledWith('fam', 200, {
      convoId: JOINT_CONVO.id,
      familyFilter: { familyId: 'fam-1', visibility: 'shared', participantUserId: 'u-me' },
    });
    const convo = useStore.getState().convos.find((c) => c.id === JOINT_CONVO.id)!;
    expect(convo.msgs.map((m) => m.t.en)).toEqual(['my earlier message', 'can anyone hear me?']);
    expect(convo.msgs[1]!.speakerUserId).toBe('u-other');
  });

  it('a forced fetch that comes back EMPTY does not wipe locally visible messages', async () => {
    // Persist-lag guard: the server persists a turn in _after_done AFTER the
    // stream closes, so a refresh inside that window can legitimately return
    // nothing — it must not blank the thread.
    listEvents.mockResolvedValueOnce([]);
    await useStore.getState().loadConvoMessages(JOINT_CONVO.id, { force: true });
    const convo = useStore.getState().convos.find((c) => c.id === JOINT_CONVO.id)!;
    expect(convo.msgs.map((m) => m.t.en)).toEqual(['my earlier message']);
  });
});

describe('touchConvo (I24/I25)', () => {
  it('sets the title from the first user message and moves the convo to the top', () => {
    // Seed a second local convo so we can observe reordering.
    useStore.getState().newChat('aria');
    const [top, second] = useStore.getState().convos;
    expect(top).toBeTruthy();
    expect(second).toBeTruthy();
    // Touch the (now second) convo with a first user message.
    useStore.getState().touchConvo(second!.id, 'I had a heavy week at work and feel stuck');
    const s = useStore.getState();
    expect(s.convos[0]!.id).toBe(second!.id); // moved to top (I25)
    // I24: title is derived from the first message and truncated to ~40 chars.
    expect(s.convos[0]!.title.en.length).toBeLessThanOrEqual(40);
    expect(s.convos[0]!.title.en.endsWith('…')).toBe(true);
    expect(s.convos[0]!.title.en.startsWith('I had a heavy week')).toBe(true);
    expect(s.convos[0]!.ts.en).toBe('now');
  });

  it('does not overwrite a real title on subsequent messages', () => {
    const id = useStore.getState().activeConvoId;
    useStore.getState().touchConvo(id, 'first message');
    useStore.getState().touchConvo(id, 'second message should not replace the title');
    const convo = useStore.getState().convos.find((c) => c.id === id)!;
    expect(convo.title.en).toBe('first message');
  });
});

describe('removeMessage (I27 retry flow)', () => {
  it('drops the error bubble so a retry can re-send without duplicating the user text', () => {
    const id = useStore.getState().activeConvoId;
    // The seeded convo already has the persona's opening message at index 0.
    const userMsg = { them: false, t: { en: 'hi', ru: 'hi' }, ts: 'now' };
    const errBubble = {
      them: true,
      t: { en: 'connection lost', ru: 'connection lost' },
      ts: 'now',
      error: true,
      retryText: 'hi',
    };
    useStore.getState().appendMessage(id, userMsg);
    useStore.getState().appendMessage(id, errBubble);
    const convo = useStore.getState().convos.find((c) => c.id === id)!;
    expect(convo.msgs).toHaveLength(3);
    expect(convo.msgs[2]!.error).toBe(true);
    expect(convo.msgs[2]!.retryText).toBe('hi');

    // retry() removes the error bubble (the user message stays) before re-sending.
    useStore.getState().removeMessage(id, convo.msgs[2]!);
    const after = useStore.getState().convos.find((c) => c.id === id)!;
    expect(after.msgs).toHaveLength(2);
    expect(after.msgs[1]!.them).toBe(false);
    expect(after.msgs[1]!.t.en).toBe('hi');
    // No leftover error bubble to re-trigger retry.
    expect(after.msgs.some((m) => m.error)).toBe(false);
  });
});
