'use client';

import { create } from 'zustand';
import {
  type ConversationSummaryRecord,
  type EventRecord,
  type FamilyMemberRecord,
  type FamilyProviderRecord,
  type FamilyRecord,
  type FamilyTherapistPromptRecord,
  deleteConvoEvents,
  getFamily,
  getFamilyTherapistPrompt,
  listConversations,
  listEvents,
} from './api-client';
import {
  CONVOS,
  type Convo,
  type Message,
  PERSONAS,
  type Persona,
  type ProviderKind,
  personaById,
} from './fixtures';

export type ActiveProvider = {
  providerId: string;
  kind: ProviderKind;
  label: string;
  keyHandle: string;
  baseUrl?: string | null;
  model?: string | null;
  // Embedding model for semantic memory recall (Provider.embeddings_model).
  // null/absent = semantic memory off — recall uses the hash embedder.
  embeddingsModel?: string | null;
};

type State = {
  railCollapsed: boolean;
  toggleRail: () => void;
  // Desktop chat sidebar (`.convos`) collapse state. On wide viewports the
  // conversation list is a persistent grid column beside the thread; this lets
  // the user fold it away to reclaim the width. Mobile ignores it — narrow
  // screens always use the slide-in drawer (driven by ChatScreen-local
  // `drawerOpen`), and the `max-width: 980px` media query overrides the grid
  // columns to a single `1fr`, so toggling this has no visual effect there.
  chatSidebarCollapsed: boolean;
  toggleChatSidebar: () => void;

  customPersonas: Persona[];
  personas: () => Persona[];

  activePersonaId: string;
  setActivePersona: (id: string) => void;
  addPersona: (p: Persona) => void;
  // In-memory edit/delete of a custom persona (no API surface — personas are
  // client-side until the persistence roadmap lands). `updatePersona` merges a
  // patch into the matching customPersona; `deletePersona` drops it and resets
  // the active persona to a builtin if the removed one was active.
  updatePersona: (id: string, patch: Partial<Persona>) => void;
  deletePersona: (id: string) => void;

  convos: Convo[];
  activeConvoId: string;
  openConvo: (id: string) => void;
  newChat: (personaId?: string) => string;
  appendMessage: (convoId: string, m: Message) => void;
  setConvoPreview: (convoId: string, text: { en: string; ru: string }) => void;
  // I24/I25: on each sent message, refresh the convo's ts, set its title from
  // the first user message (while it still reads "New conversation"), and move
  // it to the top of the drawer so the most-recent thread is always first.
  // These are local-only; the next hydrateConvos re-anchors from the server.
  touchConvo: (convoId: string, firstUserText?: string) => void;
  // I27: remove one message by reference (used by Retry to drop the failed
  // assistant bubble before re-sending the same user text).
  removeMessage: (convoId: string, msg: Message) => void;
  // I35: delete-conversation with an undo window. The UI is updated
  // optimistically (removeConvoFromList) so the row vanishes instantly; the
  // server DELETE runs after a short grace period so the user can Undo. If the
  // removal would empty the list, a fresh placeholder chat is seeded so
  // ChatScreen's convos[0] assumption never breaks — restoreConvo drops that
  // placeholder on undo; a committed delete keeps it. ``deleteConvo`` is the
  // server-only half (no list mutation): it returns false on failure so the
  // caller can toast + restoreConvo. The residual server-side memory on a
  // failed server call is the known honest limit (surfaced via the toast).
  removeConvoFromList: (
    id: string,
  ) => { convo: Convo; index: number; placeholderId: string | null } | null;
  restoreConvo: (token: { convo: Convo; index: number; placeholderId: string | null }) => void;
  deleteConvo: (convo: Convo) => Promise<boolean>;

  // K6: the conversation list is hydrated from the server (GET /v1/conversations)
  // so it survives a refresh — the server, not fixtures, is the source of truth.
  // ``hydrateConvos`` runs once on mount: on success it REPLACES the seeded
  // fixtures with the user's real conversations (a brand-new user gets one fresh
  // chat so the list is never empty). On failure (API down / offline) it leaves
  // the fixtures in place as a dev/offline fallback. ``loadConvoMessages`` lazy-
  // loads one thread's event history the first time it's opened (the list
  // projection carries title/preview, not messages). Both are best-effort and
  // never throw into the UI — a network failure just logs and leaves the list
  // as-is, so the chat keeps working offline against the local state.
  hydrated: boolean;
  hydrateConvos: () => Promise<void>;
  // ``force`` bypasses the load-once guard and re-fetches from the server —
  // used by the joint family thread's refresh (activate/focus/poll) so
  // messages other members sent after the first load are picked up. A forced
  // fetch that comes back EMPTY never wipes locally visible messages (the
  // server persists a turn in _after_done AFTER the stream closes, so a
  // refetch inside that window can legitimately lag the local view).
  loadConvoMessages: (convoId: string, opts?: { force?: boolean }) => Promise<void>;

  startChatWith: (personaId: string) => string; // returns convoId

  // New-chat persona picker modal — opened from the Rail "New chat" button and
  // the conversations drawer, so the user chooses who to start with rather than
  // silently using the active persona.
  newChatPickerOpen: boolean;
  openNewChatPicker: () => void;
  closeNewChatPicker: () => void;

  // BYOK provider currently in use (metadata only — the key lives in the vault).
  activeProvider: ActiveProvider | null;
  setActiveProvider: (p: ActiveProvider | null) => void;
  // Local mirror of a server PATCH on the active provider. Caller hits the
  // API first (e.g. ``updateProvider``) and only updates the store on success
  // so the UI never claims a persistence that didn't happen. No-op when no
  // provider is active.
  updateActiveProvider: (
    patch: Partial<Pick<ActiveProvider, 'label' | 'model' | 'baseUrl' | 'embeddingsModel'>>,
  ) => void;
  // Whether the companion recalls past events + atomic memories when composing
  // a turn (sent as `memory_on` on the stream request). Extraction runs either
  // way; this gates the salient-chains + recent-window context. Default on —
  // memory is the product's core differentiator.
  memoryOn: boolean;
  toggleMemoryOn: () => void;

  // A transient seed carried from ChatScreen's "Save to journal" hover action
  // to the Journal composer, so a chat moment becomes a diary entry without
  // long query params in the URL. Consumed + cleared by JournalScreen on mount.
  // ``eventId`` is null when the client can't see the server event id (it
  // doesn't round-trip per message); ``convoId`` is the chat thread the moment
  // came from.
  journalSeed: { personaId: string; convoId: string; eventId: string | null; text: string } | null;
  setJournalSeed: (
    seed: {
      personaId: string;
      convoId: string;
      eventId: string | null;
      text: string;
    } | null,
  ) => void;

  // Family slice — the user belongs to at most one family. ``family`` is null
  // for users who haven't created or been invited into a family.
  family: FamilyRecord | null;
  familyMembers: FamilyMemberRecord[];
  // Multi-key family BYOK: the family can hold several providers, each with
  // its own key_handle. The legacy ``familyProvider`` (singular) was the
  // active provider pointer; it's kept on the type for back-compat with the
  // many call sites that read it but the canonical source of truth is now
  // ``familyProviders`` (the list) plus ``activeFamilyProviderId`` (which row
  // is the active pointer). New code should read those two.
  familyProvider: FamilyProviderRecord | null;
  familyProviders: FamilyProviderRecord[];
  activeFamilyProviderId: string | null;
  familyInvites: {
    id: string;
    email: string;
    expires_at: string;
    created_at: string;
    accepted_at: string | null;
  }[];
  // In solo family sessions, the family therapist's recall is scoped to one
  // member (the speaker). In joint sessions this is the authenticated
  // principal themselves (each member only sees the joint channel from their
  // own lens). The picker is hidden in joint mode.
  activeFamilyMemberId: string | null;
  // The authenticated principal's user id — captured by ``loadFamily`` so the
  // read paths (``loadConvoMessages`` / ``hydrateConvos``) can pass it as
  // ``participant_user_id`` when requesting family-scoped events/conversations.
  // For the joint (shared) scope the server ignores it (any member's shared
  // row is admitted); it is a formality the wire shape requires.
  myUserId: string | null;
  // "private" = solo 1:1 with the activeFamilyMemberId member; "shared" =
  // joint with the whole family. Server-side, visibility scopes both the
  // recall predicate AND the persisted event/usage/memory family_scope.
  familySessionMode: 'private' | 'shared';
  // Owner-customised system prompt for the ``fam`` persona, read by every
  // member so they can see what their therapist is being told. ``null`` when
  // the family has not customised the prompt — readers fall back to the
  // static ``fam`` builtin on the client (see ``FAM_BUILTIN_PROMPT``). The
  // server is the source of truth; clients may be stale (a member who joined
  // after the owner edited) and the server fills the prompt in at LLM time.
  familyTherapistPrompt: FamilyTherapistPromptRecord | null;
  setFamily: (f: FamilyRecord | null) => void;
  setFamilyMembers: (m: FamilyMemberRecord[]) => void;
  setFamilyProvider: (p: FamilyProviderRecord | null) => void;
  setFamilyProviders: (ps: FamilyProviderRecord[]) => void;
  setActiveFamilyProviderId: (id: string | null) => void;
  setFamilyInvites: (
    i: {
      id: string;
      email: string;
      expires_at: string;
      created_at: string;
      accepted_at: string | null;
    }[],
  ) => void;
  setActiveFamilyMemberId: (id: string | null) => void;
  setFamilySessionMode: (m: 'private' | 'shared') => void;
  setFamilyTherapistPrompt: (p: FamilyTherapistPromptRecord | null) => void;
  // Hydrate family from /v1/family. Best-effort: a 404 (user not in a family)
  // clears the slice; other failures leave it untouched. ``myUserId`` is the
  // authenticated principal's id (passed by the auth bootstrap after a
  // successful login).
  loadFamily: (myUserId: string) => Promise<void>;
};

let customIdSeq = 1;

// Joint family session = ONE shared conversation per family, keyed by a
// deterministic convo id derived from the family id. Every member mints the
// same id, so they all read/write the same server thread and see each
// other's shared messages. (Solo `fam-solo-` 1:1 sessions stay per-member.)
// Exported for tests and for the client to derive visibility from the prefix.
export function familyJointConvoId(familyId: string): string {
  return `fam-joint-${familyId}`;
}

// Derive the family visibility of a convo from its id prefix. `fam-joint-`
// → shared (the whole-family thread), `fam-solo-` → private (a member's 1:1).
// Non-fam convos return null (personal scope, no family filter).
export function convoFamilyVisibility(convoId: string): 'shared' | 'private' | null {
  if (convoId.startsWith('fam-joint-')) return 'shared';
  if (convoId.startsWith('fam-solo-')) return 'private';
  return null;
}

// K6: format a server ISO timestamp into the drawer's short display string.
// The fixtures used "09:42" / "Yesterday" / "Mon"; here we derive the same
// shape from the conversation's last_activity so the list reads naturally in
// both locales without pulling in a date lib. Anything older than a week
// falls back to a numeric YYYY-MM-DD so the column never overflows.
function formatActivity(iso: string): { en: string; ru: string } {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { en: '', ru: '' };
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday = d.toDateString() === yesterday.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return { en: `${hh}:${mm}`, ru: `${hh}:${mm}` };
  if (isYesterday) return { en: 'Yesterday', ru: 'Вчера' };
  const dayMs = 86_400_000;
  const ageDays = (now.getTime() - d.getTime()) / dayMs;
  if (ageDays < 7) {
    const weekday = d.toLocaleDateString('en', { weekday: 'short' });
    const weekdayRu = d.toLocaleDateString('ru', { weekday: 'short' });
    return { en: weekday, ru: weekdayRu };
  }
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return { en: `${y}-${m}-${day}`, ru: `${y}-${m}-${day}` };
}

// I24: derive a convo title from its first user message. Collapses newlines,
// trims, and truncates to ~40 chars with an ellipsis so the drawer row reads
// naturally the moment the user sends the first real message (replacing the
// "New conversation" placeholder). Mirrors the server-side truncation the
// conversations projection applies, so a refresh re-anchors to the same shape.
function titleFromText(text: string): string {
  const cleaned = (text || '').trim().replace(/\n/g, ' ');
  const MAX = 40;
  if (cleaned.length <= MAX) return cleaned;
  return `${cleaned.slice(0, MAX - 1).trim()}…`;
}

// K6: map a server ConversationSummaryRecord into the UI Convo shape. The
// summary carries no messages (it's a list projection) — msgs load lazily on
// open via loadConvoMessages. title/preview are single strings truncated
// server-side; reuse the same string for both locales (the companion's
// language is a client-side concern, not stored per row).
function summaryToConvo(s: ConversationSummaryRecord): Convo {
  return {
    id: s.convo_id,
    personaId: s.persona_id,
    title: { en: s.title, ru: s.title },
    ts: formatActivity(s.last_activity),
    preview: { en: s.preview, ru: s.preview },
    msgs: [],
  };
}

// K6: map a server event row into the UI Message shape. The Event contract
// carries no created_at (it's also the mid-stream SSE token), so per-message
// timestamps aren't on the wire — ts is left empty (the honest limit; the
// convo's last_activity still anchors the list). Events arrive oldest-first.
function eventToMessage(e: EventRecord): Message {
  return {
    them: e.role === 'assistant',
    t: { en: e.content, ru: e.content },
    ts: '',
    // Carry the speaking member's id so the joint-thread renderer can
    // attribute the bubble. None on assistant events (the therapist speaks
    // as one voice to every member); absent (undefined) on non-family events.
    speakerUserId: e.participant_user_id ?? undefined,
  };
}

export const useStore = create<State>((set, get) => ({
  railCollapsed: false,
  toggleRail: () => set((s) => ({ railCollapsed: !s.railCollapsed })),

  chatSidebarCollapsed: false,
  toggleChatSidebar: () => set((s) => ({ chatSidebarCollapsed: !s.chatSidebarCollapsed })),

  customPersonas: [],
  personas: () => [...PERSONAS, ...get().customPersonas],

  activePersonaId: 'aria',
  setActivePersona: (id) => set({ activePersonaId: id }),
  addPersona: (p) => {
    set((s) => ({ customPersonas: [...s.customPersonas, p], activePersonaId: p.id }));
  },
  updatePersona: (id, patch) =>
    set((s) => ({
      customPersonas: s.customPersonas.map((p) => (p.id === id ? { ...p, ...patch } : p)),
    })),
  deletePersona: (id) =>
    set((s) => {
      const remaining = s.customPersonas.filter((p) => p.id !== id);
      const activePersonaId = s.activePersonaId === id ? 'aria' : s.activePersonaId;
      return { customPersonas: remaining, activePersonaId };
    }),

  // K6: do NOT seed fixture convos at init. The fixtures previously flashed in
  // the sidebar for the brief window before hydrateConvos() replaced them with
  // the server list ("4 чужих диалога" on chat entry). The sidebar is empty
  // until the server list lands; the offline fallback (catch block below) seeds
  // fixtures only when the API is down AND there is no local state yet.
  convos: [],
  activeConvoId: '',
  openConvo: (id) => {
    const c = get().convos.find((x) => x.id === id);
    if (c) {
      // Keep the Solo/Joint toggle in sync with the convo being opened so the
      // send path's familySessionMode fallback and the toggle UI match the
      // thread. fam-solo- → private, fam-joint- → shared; non-fam convos leave
      // the mode untouched (no family scope to reflect). Without this, opening
      // a fam-joint- convo from the sidebar left familySessionMode at its
      // previous value (default 'private'), so the toggle showed "Solo" while
      // the user was in the joint thread.
      const fv = convoFamilyVisibility(id);
      set({
        activeConvoId: id,
        activePersonaId: c.personaId,
        ...(fv ? { familySessionMode: fv } : {}),
      });
    }
  },
  newChat: (personaId) => {
    const pid = personaId ?? get().activePersonaId;
    const p = personaById(pid, get().personas());
    // Family convo scope purity: a convo never mixes solo and joint, never
    // crosses families. The id encodes the scope so a stale reference can't
    // accidentally land in the wrong predicate. (See PLAN §Family, "convo
    // never mixes scopes".)
    const family = get().family;
    const me = family ? 'me' : 'self';
    let id: string;
    // A joint convo is the family's ONE shared thread — its messages live on
    // the server under every member's user_id, so the stub must start EMPTY
    // and let loadConvoMessages fetch the real shared history. Seeding the
    // local greeting here would make msgs.length > 0 and skip the server load,
    // so a member entering the joint chat would see only the greeting, not
    // the other members' messages. Solo/non-fam convos keep the local greeting
    // (a personal new thread; the greeting is a harmless local placeholder).
    let jointFresh = false;
    if (p.id === 'fam' && family) {
      if (get().familySessionMode === 'shared') {
        // Joint session = one shared convo per family. Mint a deterministic
        // id from the family id so every member lands on the same thread,
        // and create-or-reuse the local stub (don't duplicate if it exists).
        id = familyJointConvoId(family.id);
        const existing = get().convos.find((c) => c.id === id);
        if (existing) {
          set({ activeConvoId: id, activePersonaId: p.id });
          return id;
        }
        jointFresh = true;
      } else {
        id = `fam-solo-${me}-${Date.now().toString(36)}-${customIdSeq++}`;
      }
    } else {
      id = `c${Date.now().toString(36)}${customIdSeq++}`;
    }
    const convo: Convo = {
      id,
      personaId: p.id,
      title: { en: 'New conversation', ru: 'Новый разговор' },
      ts: { en: 'now', ru: 'сейчас' },
      preview: p.open,
      msgs: jointFresh ? [] : [{ them: true, t: p.open, ts: 'now' }],
    };
    set((s) => ({ convos: [convo, ...s.convos], activeConvoId: id, activePersonaId: p.id }));
    return id;
  },
  appendMessage: (convoId, m) =>
    set((s) => ({
      convos: s.convos.map((c) => (c.id === convoId ? { ...c, msgs: [...c.msgs, m] } : c)),
    })),
  setConvoPreview: (convoId, text) =>
    set((s) => ({
      convos: s.convos.map((c) => (c.id === convoId ? { ...c, preview: text } : c)),
    })),
  removeMessage: (convoId, msg) =>
    set((s) => ({
      convos: s.convos.map((c) =>
        c.id === convoId ? { ...c, msgs: c.msgs.filter((mm) => mm !== msg) } : c,
      ),
    })),
  touchConvo: (convoId, firstUserText) =>
    set((s) => {
      const idx = s.convos.findIndex((c) => c.id === convoId);
      if (idx === -1) return {};
      const convo = s.convos[idx]!;
      const isPlaceholder = convo.title.en === 'New conversation' || convo.title.en === '';
      const title =
        isPlaceholder && firstUserText
          ? { en: titleFromText(firstUserText), ru: titleFromText(firstUserText) }
          : convo.title;
      const touched: Convo = { ...convo, title, ts: { en: 'now', ru: 'сейчас' } };
      // I25: move the most-recent thread to the top.
      const next = [touched, ...s.convos.filter((c) => c.id !== convoId)];
      return { convos: next };
    }),
  // I35: optimistic delete. Drops the convo from the drawer immediately and
  // returns a token the caller can pass to restoreConvo to undo, or to
  // deleteConvo to commit the server-side removal. If this empties the list, a
  // fresh placeholder chat is seeded (id returned as placeholderId) so
  // ChatScreen's convos[0] assumption never breaks — restoreConvo drops it.
  removeConvoFromList: (id) => {
    const s = get();
    const idx = s.convos.findIndex((c) => c.id === id);
    if (idx === -1) return null;
    const convo = s.convos[idx]!;
    const remaining = s.convos.filter((c) => c.id !== id);
    if (remaining.length === 0) {
      // Never leave the list empty — seed a fresh placeholder with the active
      // persona. On undo restoreConvo drops it; on a committed delete it stays.
      const p = personaById(s.activePersonaId, s.personas());
      const placeholderId = `c${Date.now().toString(36)}${customIdSeq++}`;
      const placeholder: Convo = {
        id: placeholderId,
        personaId: p.id,
        title: { en: 'New conversation', ru: 'Новый разговор' },
        ts: { en: 'now', ru: 'сейчас' },
        preview: p.open,
        msgs: [{ them: true, t: p.open, ts: 'now' }],
      };
      set({ convos: [placeholder], activeConvoId: placeholderId, activePersonaId: p.id });
      return { convo, index: idx, placeholderId };
    }
    const activeConvoId = s.activeConvoId === id ? remaining[0]!.id : s.activeConvoId;
    const activePersonaId = s.activeConvoId === id ? remaining[0]!.personaId : s.activePersonaId;
    set({ convos: remaining, activeConvoId, activePersonaId });
    return { convo, index: idx, placeholderId: null };
  },

  // I35: undo an optimistic removal. Drops the placeholder (if one was seeded)
  // and re-inserts the original convo at its prior index (clamped). Restores
  // it as the active convo so the user lands back where they were.
  restoreConvo: (token) => {
    const s = get();
    const base = token.placeholderId
      ? s.convos.filter((c) => c.id !== token.placeholderId)
      : s.convos;
    const idx = Math.min(token.index, base.length);
    const convos = [...base.slice(0, idx), token.convo, ...base.slice(idx)];
    set({ convos, activeConvoId: token.convo.id, activePersonaId: token.convo.personaId });
  },

  // I35: server-only delete — the UI list was already updated optimistically
  // (removeConvoFromList). Best-effort: a failed server call returns false so
  // the caller can surface a toast and restoreConvo. The residual server-side
  // memory is the known honest limit, now surfaced to the user via the toast.
  deleteConvo: async (convo) => {
    try {
      await deleteConvoEvents(convo.personaId, convo.id);
      return true;
    } catch (err) {
      console.warn('deleteConvoEvents failed; server memory may persist', err);
      return false;
    }
  },

  hydrated: false,
  hydrateConvos: async () => {
    if (get().hydrated) return;
    try {
      const summaries = await listConversations();
      // Joint family convo: the shared thread lives under every member's
      // user_id, so a member who hasn't yet sent a message in it won't see it
      // in the personal-only call above. When the principal is in a family,
      // also fetch the family SHARED convo list and merge it in (deduped by
      // convo id) so the joint channel is always present in the sidebar.
      const family = get().family;
      const myUserId = get().myUserId;
      const sharedSummaries =
        family && myUserId
          ? await listConversations(undefined, undefined, 50, {
              familyId: family.id,
              visibility: 'shared',
              participantUserId: myUserId,
            }).catch(() => [])
          : [];
      const merged: typeof summaries = [];
      const seen = new Set<string>();
      for (const s of [...summaries, ...sharedSummaries]) {
        if (seen.has(s.convo_id)) continue;
        seen.add(s.convo_id);
        merged.push(s);
      }
      // The server is the source of truth — replace the seeded fixtures with
      // the user's real conversations. A brand-new user (empty list) gets one
      // fresh chat so the list is never empty (ChatScreen assumes convos[0]).
      if (merged.length === 0) {
        set({ convos: [], hydrated: true });
        get().newChat();
        return;
      }
      const serverConvos = merged.map(summaryToConvo);
      // Preserve the active convo if it still exists server-side; otherwise
      // fall back to the most-recent (summaries arrive last_activity desc).
      const prevActive = get().activeConvoId;
      const stillThere = serverConvos.find((c) => c.id === prevActive);
      const activeConvo = stillThere ?? serverConvos[0]!;
      // Sync the Solo/Joint toggle to the active convo (see openConvo): a
      // fam-joint- active convo must show "Joint" / send shared, a fam-solo-
      // convo "Solo" / private. Non-fam active convos leave the mode as-is.
      const fv = convoFamilyVisibility(activeConvo.id);
      set({
        convos: serverConvos,
        activeConvoId: activeConvo.id,
        activePersonaId: activeConvo.personaId,
        ...(fv ? { familySessionMode: fv } : {}),
        hydrated: true,
      });
    } catch (err) {
      // API down / offline: if there's no local state yet, seed the fixtures as a
      // dev/offline fallback so the sidebar isn't empty. Existing convos (e.g. a
      // just-minted local chat) are left in place. Do NOT set hydrated so a later
      // retry can still hydrate.
      if (get().convos.length === 0) {
        set({ convos: CONVOS.map((c) => ({ ...c })), activeConvoId: 'c1' });
      }
      console.warn('hydrateConvos failed; using fixture fallback', err);
    }
  },
  loadConvoMessages: async (convoId, opts) => {
    const convo = get().convos.find((c) => c.id === convoId);
    // Only load once per convo — skip if it already has messages OR was
    // created locally this session (server has nothing for it yet). A convo
    // with msgs.length === 0 that came from hydration needs its history.
    // ``force`` (the joint family thread's refresh) bypasses this guard:
    // other members' messages can arrive at any time, so the shared thread
    // must re-fetch, not load once and go stale for the rest of the session.
    if (!convo || (convo.msgs.length > 0 && !opts?.force)) return;
    try {
      // Joint (fam-joint-) convo: request the family SHARED scope so the
      // server returns every member's shared messages in the thread, not just
      // the requester's own. Solo (fam-solo-) and non-fam convos keep the
      // unfiltered load — the principal's own rows in that convo, which for
      // a solo thread may include messages spoken as different members.
      const visibility = convoFamilyVisibility(convo.id);
      const family = get().family;
      const myUserId = get().myUserId;
      const familyFilter =
        visibility === 'shared' && family && myUserId
          ? { familyId: family.id, visibility: 'shared' as const, participantUserId: myUserId }
          : undefined;
      const events = await listEvents(convo.personaId, 200, {
        convoId: convo.id,
        familyFilter,
      });
      if (events.length === 0) {
        // Brand-new joint thread (no shared messages yet): seed the persona's
        // opening line so the thread isn't an empty composer. The joint stub
        // starts with msgs: [] (so this fetch runs at all); solo/non-fam convos
        // keep their locally-seeded greeting and never reach here (msgs > 0).
        // A FORCED refresh that comes back empty must NOT wipe or re-seed over
        // locally visible messages — the server persists a turn post-stream
        // (_after_done), so an empty reply here can just be persist lag.
        if (visibility === 'shared' && convo.msgs.length === 0) {
          const p = personaById(convo.personaId, get().personas());
          set((s) => ({
            convos: s.convos.map((c) =>
              c.id === convoId ? { ...c, msgs: [{ them: true, t: p.open, ts: 'now' }] } : c,
            ),
          }));
        }
        return;
      }
      const msgs = events.map(eventToMessage);
      set((s) => ({
        convos: s.convos.map((c) => (c.id === convoId ? { ...c, msgs } : c)),
      }));
    } catch (err) {
      // Best-effort: a failed history load leaves the thread empty (the user
      // can still send a new message). Don't throw into the UI.
      console.warn('loadConvoMessages failed; thread stays empty', err);
    }
  },

  startChatWith: (personaId) => {
    set({ activePersonaId: personaId });
    return get().newChat(personaId);
  },

  newChatPickerOpen: false,
  openNewChatPicker: () => set({ newChatPickerOpen: true }),
  closeNewChatPicker: () => set({ newChatPickerOpen: false }),

  activeProvider: null,
  setActiveProvider: (p) => set({ activeProvider: p }),
  updateActiveProvider: (patch) =>
    set((s) => (s.activeProvider ? { activeProvider: { ...s.activeProvider, ...patch } } : {})),

  memoryOn: true,
  toggleMemoryOn: () => set((s) => ({ memoryOn: !s.memoryOn })),

  journalSeed: null,
  setJournalSeed: (seed) => set({ journalSeed: seed }),

  // --- family slice ---
  family: null,
  familyMembers: [],
  familyProvider: null,
  familyProviders: [],
  activeFamilyProviderId: null,
  familyInvites: [],
  activeFamilyMemberId: null,
  myUserId: null,
  familySessionMode: 'private',
  familyTherapistPrompt: null,
  setFamily: (f) => set({ family: f }),
  setFamilyMembers: (m) => set({ familyMembers: m }),
  setFamilyProvider: (p) => set({ familyProvider: p }),
  setFamilyProviders: (ps) => set({ familyProviders: ps }),
  setActiveFamilyProviderId: (id) => set({ activeFamilyProviderId: id }),
  setFamilyInvites: (i) => set({ familyInvites: i }),
  setActiveFamilyMemberId: (id) => set({ activeFamilyMemberId: id }),
  setFamilySessionMode: (m) => set({ familySessionMode: m }),
  setFamilyTherapistPrompt: (p) => set({ familyTherapistPrompt: p }),
  loadFamily: async (myUserId) => {
    try {
      // Fetch the family + the owner-customised therapist prompt in
      // parallel — they are independent reads and the therapist prompt is
      // also a member-visible surface (audit). ``getFamilyTherapistPrompt``
      // returns 200 for members (with body=null when unset) and 404 ONLY
      // for non-members; since ``getFamily`` has already resolved we're
      // past the 404 gate, so a transient 404 here is treated as "still
      // loading" and the slice is left at its default.
      //
      // The full family provider list is read from the same ``getFamily``
      // response (the server returns ``providers: FamilyProvider[]`` —
      // multi-key surface since the BYOK upgrade). Avoid a separate
      // ``listFamilyProviders`` call here: this runs on every auth
      // bootstrap and an extra round-trip pushes the auth-gated screens
      // (ChatScreen) over their mount-tick budget in the test suite.
      const [state, therapistPrompt] = await Promise.all([
        getFamily(),
        getFamilyTherapistPrompt().catch(() => null),
      ]);
      const providers = state.providers ?? [];
      set({
        family: state.family,
        familyMembers: state.members,
        familyInvites: state.invites,
        familyProvider: state.provider,
        familyProviders: providers,
        // Default the active pointer to the legacy single-provider record if
        // it exists, else the first list entry, else null. The family has no
        // per-user active pointer (one key surface shared by members).
        activeFamilyProviderId: state.provider?.id ?? providers[0]?.id ?? null,
        familyTherapistPrompt: therapistPrompt,
        // Default the solo picker to "me" — a member is always at least their
        // own audience. The user can switch via the picker above the composer.
        activeFamilyMemberId: myUserId,
        myUserId,
      });
    } catch (err) {
      // 404 = user is not in a family; clear the slice. Other failures are
      // non-fatal (the next call will retry) — don't blank the family just
      // because the network blipped.
      if (err instanceof Error && /404/.test(err.message)) {
        set({
          family: null,
          familyMembers: [],
          familyInvites: [],
          familyProvider: null,
          familyProviders: [],
          activeFamilyProviderId: null,
          familyTherapistPrompt: null,
          activeFamilyMemberId: null,
          myUserId: null,
        });
      }
    }
  },
}));
