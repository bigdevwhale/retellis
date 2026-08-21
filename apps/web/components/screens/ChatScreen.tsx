'use client';

import { Markdown } from '@/components/Markdown';
import { ChatSkeleton } from '@/components/ui/ChatSkeleton';
import { getFamily, isTransientOrNetworkError, listProviders } from '@/lib/api-client';
import { useAuthCtx } from '@/lib/auth';
import { type Message, personaById } from '@/lib/fixtures';
import { useLang } from '@/lib/i18n';
import { streamChat } from '@/lib/llm-client';
import { explainLlmError } from '@/lib/llm-errors';
import { stripMarkdown } from '@/lib/markdown';
import { resetFamilyVault, resetPersonalVault } from '@/lib/reset';
import { convoFamilyVisibility, useStore } from '@/lib/store';
import { toast } from '@/lib/toast';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { type CSSProperties, useEffect, useMemo, useRef, useState } from 'react';

const KIND_LABEL: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  openrouter: 'OpenRouter',
  ollama: 'Ollama',
  mock: 'local fallback',
};

function now() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// Joint family thread live-refresh cadence: slow poll while the shared convo
// is active (there is no push channel — fetch is the only way to learn about
// other members' messages), plus a short post-send cooldown covering the
// server's post-stream _after_done persist window so a refresh can't come
// back without the just-finished exchange and flicker it away.
const JOINT_POLL_MS = 8000;
const JOINT_SEND_COOLDOWN_MS = 4000;

export function ChatScreen() {
  const { t, L2 } = useLang();
  const convos = useStore((s) => s.convos);
  const activeConvoId = useStore((s) => s.activeConvoId);
  const activePersonaId = useStore((s) => s.activePersonaId);
  const appendMessage = useStore((s) => s.appendMessage);
  const removeMessage = useStore((s) => s.removeMessage);
  const setConvoPreview = useStore((s) => s.setConvoPreview);
  const newChat = useStore((s) => s.newChat);
  const hydrateConvos = useStore((s) => s.hydrateConvos);
  const loadConvoMessages = useStore((s) => s.loadConvoMessages);
  const touchConvo = useStore((s) => s.touchConvo);
  const openNewChatPicker = useStore((s) => s.openNewChatPicker);
  const chatSidebarCollapsed = useStore((s) => s.chatSidebarCollapsed);
  const toggleChatSidebar = useStore((s) => s.toggleChatSidebar);
  const personas = useStore((s) => s.personas);
  const activeProvider = useStore((s) => s.activeProvider);
  const memoryOn = useStore((s) => s.memoryOn);
  const toggleMemoryOn = useStore((s) => s.toggleMemoryOn);
  const setJournalSeed = useStore((s) => s.setJournalSeed);
  // Family slice — used only when the active persona is `fam`. Reads from the
  // store directly (not via subscriptions) inside send() so we always see the
  // freshest member pick / session mode at the moment of the click.
  const family = useStore((s) => s.family);
  const familyMembers = useStore((s) => s.familyMembers);
  const familyProvider = useStore((s) => s.familyProvider);
  const activeFamilyMemberId = useStore((s) => s.activeFamilyMemberId);
  const familySessionMode = useStore((s) => s.familySessionMode);
  const setActiveFamilyMemberId = useStore((s) => s.setActiveFamilyMemberId);
  const setFamilySessionMode = useStore((s) => s.setFamilySessionMode);
  const router = useRouter();
  // Auth context — used by the family lockout banner to gate the
  // "Forgot? Reset" affordance to the family owner (non-owners use
  // "Leave family" instead, which is a different destructive action).
  const { principal, config, loading: authLoading } = useAuthCtx();
  // Hosted = billing on. Used for lazy onboarding: on hosted a missing
  // *personal* key is not a hard lockout — the routing chain falls through to
  // env keys and MockAdapter, so the app always answers. We show a soft nudge
  // and keep the composer enabled instead of disabling chat.
  // Keyed on the deployment MODE, not `features.billing`: the hosted trial
  // path (operator-paid OpenRouter env fallback + trial credits) serves real
  // turns without billing being configured (FEATURE_BILLING=0). Tying this to
  // `billing` would hard-lock a fresh hosted signup out of chat.
  const hosted = config?.mode === 'hosted';

  const convo = convos.find((c) => c.id === activeConvoId) ?? convos[0];
  const persona = personaById(convo?.personaId ?? activePersonaId, personas());
  // True when no BYOK provider row exists for this turn. The key lives
  // server-side now (envelope-encrypted), so "no key" = "no provider row" —
  // no client-side vault to be locked. Surfaced so the user knows to connect
  // a key rather than mistaking a server-fallback reply for a real provider.
  // For the family persona the family provider must exist.
  const isFam = persona.id === 'fam';
  // Family key-satisfaction depends on the family's `use_owner_personal_key`
  // flag: when on, the family rides the owner's active personal provider
  // (activeProvider.keyHandle) instead of a family_providers row.
  const noKey = isFam
    ? !family ||
      (family.use_owner_personal_key ? !activeProvider?.keyHandle : !familyProvider?.key_handle)
    : !activeProvider?.keyHandle;
  // Soft nudge (hosted only, personal scope): no BYOK key, but chat is not
  // disabled — env/mock serves the turn. Family no-key stays a hard lockout on
  // both modes (a shared family key is a real prerequisite, not deferrable).
  const softNudge = hosted && !isFam && noKey;

  const [input, setInput] = useState('');
  const [typing, setTyping] = useState(false);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [fallbackNotice, setFallbackNotice] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const accRef = useRef('');
  // Guards the window between send() entry and the server's `session` SSE
  // event. `streaming` only flips to '' on that event (after a network RTT),
  // so without this ref a second Enter/click during that gap re-enters send()
  // with streaming===null and fires a duplicate request. See K2.
  const sendingRef = useRef(false);
  // True once a turn has produced its final bubble / cleared in-flight UI
  // state (via done/error/catch/stop). The finally block checks it to
  // recover from a stream that ended without a `done` event (K5) without
  // double-finalizing. Reset to false at the start of each send().
  const finalizedRef = useRef(false);
  // Timestamp of the last completed/aborted send() — the joint thread's
  // refresh skips this window so it can't fetch the shared history before
  // the server has persisted the just-finished turn (_after_done runs after
  // the stream closes) and briefly drop it from view.
  const lastSendAtRef = useRef(0);
  // Whether the user is currently pinned to the bottom of the stream. Updated
  // by the onScroll handler on every scroll — the user's own scroll position
  // is the source of truth for "am I at the bottom?", NOT a post-append
  // distance measurement (which is wrong: the just-appended bubble itself
  // pushes scrollHeight past the threshold and suppresses the very scroll we
  // want). Reset to true on conversation switch so a newly-opened thread
  // scrolls to the latest instead of inheriting the previous thread's
  // scrolled-up state.
  const pinnedRef = useRef(true);
  const onStreamScroll = () => {
    const el = streamRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    if (!convo) newChat(activePersonaId);
  }, [convo, activePersonaId, newChat]);

  // K6: hydrate the conversation list from the server once on mount so the
  // drawer survives a refresh (the server, not fixtures, is the source of
  // truth). hydrateConvos is idempotent — it no-ops once `hydrated` is set.
  // Wait for the auth bootstrap: it awaits loadFamily(), and the family slice
  // (family + myUserId) drives the joint-convo shared-scope merge — hydrating
  // before it lands would skip the merge for the whole session.
  useEffect(() => {
    if (authLoading) return;
    void hydrateConvos();
  }, [hydrateConvos, authLoading]);

  // K6: lazy-load the active thread's event history the first time it's
  // opened. Hydrated convos carry title/preview but no messages; a locally-
  // created convo already has its opening message, so loadConvoMessages skips
  // it. Best-effort — a failure leaves the thread empty (the user can still
  // send a new message), never throws into the UI.
  // Also gated on the auth bootstrap: for a joint (fam-joint-) convo the load
  // needs the family slice to build the shared-scope filter — without it the
  // fetch asks for the PERSONAL scope, other members' rows are excluded, and
  // the (wrong) result would stick for the rest of the session.
  useEffect(() => {
    if (authLoading) return;
    if (!convo || convo.msgs.length > 0) return;
    void loadConvoMessages(convo.id);
  }, [convo, loadConvoMessages, authLoading]);

  // Joint family thread live-refresh. The shared convo is one thread per
  // family written by EVERY member, but the client only learns about new
  // messages by fetching — there is no push channel. Without a refresh the
  // load-once guard meant a member never saw what others sent after their
  // first load (the recurring "I can't see other members' messages" bug).
  // Refresh on activation + window focus + a slow poll while the tab is
  // visible. Never refresh mid-turn (it would clobber the optimistic user
  // bubble / streaming reply) or right after one (the server persists the
  // turn in _after_done AFTER the stream closes — give it a beat).
  const jointActiveCid = convo && convoFamilyVisibility(convo.id) === 'shared' ? convo.id : null;
  useEffect(() => {
    if (authLoading || !jointActiveCid) return;
    const cid = jointActiveCid;
    const refresh = () => {
      if (sendingRef.current || streaming !== null || typing) return;
      if (Date.now() - lastSendAtRef.current < JOINT_SEND_COOLDOWN_MS) return;
      void loadConvoMessages(cid, { force: true });
    };
    refresh();
    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    const iv = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, JOINT_POLL_MS);
    return () => {
      window.removeEventListener('focus', onFocus);
      clearInterval(iv);
    };
    // Deps are stable primitives only (string ids / booleans) — NOT `convo`:
    // each refresh replaces the convos array, so an object dep would re-fire
    // the effect after every fetch and loop the network.
  }, [authLoading, jointActiveCid, loadConvoMessages, streaming, typing]);

  useEffect(() => {
    const msgCount = convo?.msgs.length ?? 0;
    if (msgCount === 0 && !typing && streaming === null) return;
    const el = streamRef.current;
    if (!el) return;
    // Only auto-follow when the user is pinned to the bottom — otherwise
    // yanking them down mid-read (history scroll-up) is jarring. `pinnedRef`
    // is maintained by the onScroll handler and reflects the user's position
    // BEFORE this render's new content was appended, so the just-added
    // bubble can't push us past the threshold and suppress the scroll we
    // actually want (the old post-append distance check did exactly that — a
    // bubble taller than ~80px made nearBottom false at send time, so the
    // chat never auto-scrolled on send). While streaming, use instant
    // scroll: the CSS `scroll-behavior: smooth` would queue a smooth
    // animation per token and look segued/janky.
    if (!pinnedRef.current) return;
    if (streaming !== null || typing) {
      el.scrollTo({ top: el.scrollHeight, behavior: 'auto' });
    } else {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
  }, [convo?.id, convo?.msgs.length, typing, streaming]);

  // K3: switching conversation mid-stream used to leak — streaming/typing
  // and accRef/abortRef are component-level, not per-convo, so the streaming
  // bubble rendered in the wrong conversation and the old AbortController
  // became unreachable (network/connection leak). On any activeConvoId
  // change (and unmount) abort the in-flight turn and reset the streaming
  // UI. The old turn's partial is intentionally discarded: the user moved
  // on. finalizedRef is set so the aborted send()'s finally skips its
  // finalize(accRef.current) — otherwise it would append the partial back
  // into the NEW convo (cid is captured at send time, but finalize reads
  // accRef which we just cleared, and the append would still target the
  // old cid; setting finalized keeps it from running at all).
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset-on-change idiom — the dep exists only to re-trigger the returned cleanup on activeConvoId change (and unmount); the value is intentionally unused in the body.
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      sendingRef.current = false;
      finalizedRef.current = true;
      accRef.current = '';
      setStreaming(null);
      setTyping(false);
    };
  }, [activeConvoId]);

  // Reset the pin on conversation switch: a freshly-opened thread should
  // scroll to the latest rather than inherit the previous thread's scrolled-
  // up state (pinnedRef would otherwise stay false if the user had scrolled up
  // to read history in the thread they just left).
  useEffect(() => {
    pinnedRef.current = true;
  }, [activeConvoId]);

  // Provider re-discovery for the lockout banner. After a page refresh the
  // in-memory `activeProvider` is wiped, but the server-side provider rows
  // may still exist (keys live server-side now). We poll listProviders once
  // on mount so the banner can distinguish two sub-cases:
  //
  //   1. A provider row exists but activeProvider isn't hydrated yet →
  //      hydrate it and the banner clears.
  //   2. No provider row → "go to Onboarding" CTA.
  //
  // I30: a 5xx/network failure on the discovery call is surfaced as "can't
  // reach the server" rather than collapsing to a misleading "no key".
  const [hasRemoteProvider, setHasRemoteProvider] = useState<boolean | null>(null);
  const [discoveryError, setDiscoveryError] = useState<boolean>(false);
  useEffect(() => {
    if (activeProvider?.keyHandle) {
      setHasRemoteProvider(true);
      setDiscoveryError(false);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const remote = await listProviders();
        if (!alive) return;
        setDiscoveryError(false);
        setHasRemoteProvider(remote.length > 0);
        // Hydrate activeProvider from the first row so the banner clears
        // without a manual refresh after a page reload.
        if (remote.length > 0 && !useStore.getState().activeProvider) {
          const first = remote[0]!;
          useStore.getState().setActiveProvider({
            providerId: first.id,
            kind: first.kind,
            label: first.label,
            keyHandle: first.key_handle ?? '',
            baseUrl: first.base_url,
            model: first.model,
            embeddingsModel: first.embeddings_model,
          });
        }
      } catch (e) {
        if (!alive) return;
        if (isTransientOrNetworkError(e)) {
          setDiscoveryError(true);
          setHasRemoteProvider(null);
        } else {
          // 4xx — treat as "no provider" (401 is handled by the auth gate;
          // 404 etc. legitimately mean empty).
          setDiscoveryError(false);
          setHasRemoteProvider(false);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [activeProvider]);

  // "Reset" confirm state. The destructive path deletes the server-side
  // provider rows (and for the family case clears the family vault
  // metadata so the owner can re-mint a new family key). The confirm
  // needs a typed value to prevent an accidental click from wiping
  // connected keys. For the personal case the typed value is the literal
  // word "RESET"; for the family case it is the family name verbatim.
  const [resetOpen, setResetOpen] = useState(false);
  const [resetTyped, setResetTyped] = useState('');
  const [resetErr, setResetErr] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const doReset = async () => {
    setResetErr(null);
    setResetting(true);
    try {
      if (lockoutBanner?.resetKind === 'family') {
        await resetFamilyVault();
      } else {
        await resetPersonalVault();
      }
      // Close the confirm and clear the banner's local discovery flags so
      // the effect re-fetches and the banner re-derives to the empty-state.
      setResetOpen(false);
      setResetTyped('');
      setHasRemoteProvider(null);
    } catch (e) {
      setResetErr(
        L2({
          en: `Reset failed: ${e instanceof Error ? e.message : String(e)}`,
          ru: `Сброс не удался: ${e instanceof Error ? e.message : String(e)}`,
        }),
      );
    } finally {
      setResetting(false);
    }
  };

  const autosize = () => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  };

  // I27: ``opts.retryText`` re-runs a failed turn verbatim — the original user
  // message is already in the thread, so we skip the user-bubble append, the
  // preview/title touch, and the input clear. The failed assistant bubble is
  // removed by the caller (Retry button) before invoking this.
  const send = async (opts?: { retryText?: string; requestId?: string }) => {
    if (!convo) return;
    const isRetry = opts?.retryText !== undefined;
    const text = (opts?.retryText ?? input).trim();
    if (!text || streaming !== null || sendingRef.current) return;
    // I8: idempotency key for this turn. A fresh turn mints a new id; a retry
    // reuses the ORIGINAL turn's id (passed via opts.requestId from the error
    // bubble) so the server dedups persistence by (user_id, convo_id,
    // request_id) and a retried turn never forks the event chain. ``crypto
    // .randomUUID`` is available in the browser and in the Node/vitest test
    // runtime (>=20).
    const requestId = opts?.requestId ?? crypto.randomUUID();
    // Flip synchronously BEFORE any await so a second Enter/click in the
    // gap before the server's `session` event can't start a duplicate
    // stream. Cleared in the finally below.
    sendingRef.current = true;
    const cid = convo.id;
    if (!isRetry) {
      const meMsg: Message = {
        them: false,
        t: { en: text, ru: text },
        ts: now(),
        // Attribute the locally-sent bubble to the speaking member so the
        // joint-thread renderer labels it (and so other members, reloading
        // the shared thread, see the right author). ``activeFamilyMemberId``
        // defaults to the principal in loadFamily and the picker is disabled
        // in joint mode, so this is "me" speaking; in solo the picker may name
        // another member, but solo convos don't render captions anyway.
        speakerUserId: isFam ? (activeFamilyMemberId ?? undefined) : undefined,
      };
      appendMessage(cid, meMsg);
      setConvoPreview(cid, { en: text, ru: text });
      // I24/I25: the drawer reflects the send — refresh ts, set the title from
      // this first user message if the convo still reads "New conversation",
      // and move the thread to the top so the most-recent is always first.
      touchConvo(cid, text);
      setInput('');
      requestAnimationFrame(autosize);
    }
    setTyping(true);
    accRef.current = '';
    finalizedRef.current = false;
    setFallbackNotice(null);

    // Family turns: refresh the ENTIRE family slice right before building
    // the wire body so family_id, family_key_handle, and
    // participant_user_id all agree on the same family. Without this, a
    // store snapshot from a previous family (e.g. after disband +
    // re-create, or an invite accepted in another tab) can send a stale
    // family_id, which the server rejects with 404 "family not found"
    // (cross-family access guard). Refreshing only `family` is NOT enough:
    // `familyProvider` / `activeFamilyMemberId` are captured at render
    // time and would still point at the OLD family. The body would then
    // carry a fresh family_id paired with a stale family_key_handle (and
    // possibly a stale participant_user_id), and the server's cross-family
    // guard only checks family_id — so the wrong family's API key would
    // silently serve the turn. The cost is one GET /v1/family per family
    // turn — negligible vs the LLM call.
    if (isFam) {
      const prevFamilyId = useStore.getState().family?.id;
      try {
        const fresh = await getFamily();
        // Write the WHOLE slice atomically from one server response so
        // family + familyProvider + familyMembers + the member picker
        // cannot diverge. The solo picker (activeFamilyMemberId) is an
        // exception: it's the user's explicit, per-conversation choice, so
        // PRESERVE it across same-family turns. Reset to "me" only when the
        // family actually changed (a stale picker could otherwise point at
        // a member of the OLD family, which the server rejects as a
        // cross-family participant) OR when it's null (first load). This
        // stops the picker from snapping back to "me" on every send.
        const prevPicker = useStore.getState().activeFamilyMemberId;
        const familyChanged = fresh.family?.id !== prevFamilyId;
        const nextPicker =
          familyChanged || prevPicker == null ? (principal?.user_id ?? null) : prevPicker;
        useStore.setState({
          family: fresh.family,
          familyMembers: fresh.members,
          familyInvites: fresh.invites,
          familyProvider: fresh.provider,
          activeFamilyMemberId: nextPicker,
        });
      } catch (err) {
        // 404 = no longer in a family; clear the slice so the body omits
        // family_id and the server falls back to the personal path.
        // Other failures (5xx, network) are NOT silently retried here:
        // proceeding would send a stale cross-family body — exactly the
        // risk the comment at the top of this block warns about. Abort the
        // turn with a plain localized in-bubble message instead.
        if (err instanceof Error && /404/.test(err.message)) {
          useStore.setState({
            family: null,
            familyMembers: [],
            familyInvites: [],
            familyProvider: null,
            activeFamilyMemberId: null,
          });
        } else {
          setTyping(false);
          setStreaming(null);
          sendingRef.current = false;
          appendMessage(cid, {
            them: true,
            t: {
              en: "Couldn't refresh your family session. Try again.",
              ru: 'Не удалось обновить семейную сессию. Попробуйте снова.',
            },
            ts: now(),
          });
          return;
        }
      }
      // If the family identity changed, the stale familyProvider points at
      // the OLD family's key — ABORT the turn instead of silently serving the
      // wrong family's key. The server's cross-family guard would 404, but
      // surfacing a plain in-bubble message makes the failure obvious rather
      // than a silent fallback. The user should re-engage the new family's
      // key from /family.
      if (useStore.getState().family?.id !== prevFamilyId) {
        setTyping(false);
        setStreaming(null);
        sendingRef.current = false;
        appendMessage(cid, {
          them: true,
          t: {
            en: 'Your family changed — reconnect the family key to continue.',
            ru: 'Семья сменилась — переподключите семейный ключ, чтобы продолжить.',
          },
          ts: now(),
        });
        return;
      }
    }
    // Re-read the whole family slice from the store so the body uses the
    // refreshed values (the closure's `family` / `familyProvider` /
    // `activeFamilyMemberId` are captured at render time and would be
    // stale after the awaits above).
    const famSlice = useStore.getState();
    const familyForBody = famSlice.family;
    const familyProviderForBody = famSlice.familyProvider;
    const activeMemberForBody = famSlice.activeFamilyMemberId;
    // The principal's user id — the speaker in a joint (shared) turn. Read
    // fresh (the render-time `principal?.user_id` closure can be stale across
    // the awaits above); the server requires participant_user_id == principal.
    const myUserIdForBody = famSlice.myUserId;
    // The owner's personal active provider — read fresh so the family turn
    // rides the current personal key/model when the family's
    // `use_owner_personal_key` flag is on (the owner may switch personal keys
    // in /onboarding or Settings between renders).
    const activeProviderForBody = famSlice.activeProvider;
    const famUsesPersonalKey = !!familyForBody?.use_owner_personal_key;

    // The server now resolves the BYOK key from its envelope store
    // (`providers.api_key_ciphertext`), so the client sends null for both
    // `enc_key_blob` / `family_enc_key_blob` and just carries the key_handle
    // so the server knows which provider row to read. Family and personal
    // turns are mutually exclusive on the wire (server returns 400 if both
    // key_handles are sent in the same turn).
    //
    // Family turns have two key modes (selected by the family's
    // `use_owner_personal_key` flag): default → the family_providers row's
    // key_handle; flag-on → the OWNER's personal providers key_handle, sent
    // via the same `family_key_handle` field. The server resolves it from
    // the owner's personal ciphertext (using fam.owner_user_id, never a
    // client value) — so members can't retarget the lookup.
    let enc_key_blob: string | null = null;
    let key_handle: string | null = null;
    let family_enc_key_blob: string | null = null;
    let family_key_handle: string | null = null;
    let family_id: string | null = null;
    let visibility: 'private' | 'shared' | null = null;
    let participant_user_id: string | null = null;
    if (isFam && familyForBody) {
      // Family turn. The family SCOPE (family_id / visibility /
      // participant_user_id) is sent on EVERY family turn — independent of
      // whether a family/owner BYOK key is configured. A keyless family turn
      // falls through to env / ollama / mock on the server, but its events
      // MUST still persist under the family scope so every member sees them
      // in the joint thread. Coupling scope to the key handle (the old
      // `if (curMember && famHandle)` gate) silently stored keyless joint
      // turns as personal/private, hiding them from other members — the
      // reported "I can't see other members' messages" symptom when the
      // family had no key configured.
      // The key handle is orthogonal: sent only when a family/owner key
      // exists. The handle is the family provider's (default) or the owner's
      // active personal provider's (flag-on). The active member is set
      // (activeFamilyMemberId defaults to "me" in loadFamily); in joint mode
      // participant_user_id is the principal, and the server scopes recall
      // to shared only.
      const curMember = activeMemberForBody;
      const famHandle = famUsesPersonalKey
        ? activeProviderForBody?.keyHandle
        : familyProviderForBody?.key_handle;
      // Derive visibility from the convo id — the ground truth for which
      // thread the message lands in AND which scope the load path reads
      // (loadConvoMessages uses convoFamilyVisibility(convo.id) too). The
      // separately-toggleable ``familySessionMode`` field is NOT kept in sync
      // when a member opens an existing fam-joint- convo from the sidebar
      // (openConvo only sets activeConvoId), so reading it here stored those
      // turns as PRIVATE under the joint convo id — other members' shared
      // reads then saw nothing, the reported "I can't see other members'
      // messages" bug. Falling back to the toggle only for legacy non-prefixed
      // fam convos. The id is also the source of truth on the server: the
      // event is persisted under ``cid`` and recalled by ``cid``.
      const cidVis = convoFamilyVisibility(cid);
      const vis = cidVis ?? familySessionMode;
      if (curMember && vis) {
        family_id = familyForBody.id;
        visibility = vis;
        // Joint (shared): the speaker is the principal (the server requires
        // participant_user_id == principal; the member picker is disabled in
        // joint mode so curMember is the principal, but force it in case a
        // stale solo picker value lingers across an openConvo). Solo: the
        // picked member (defaults to the principal via loadFamily).
        participant_user_id = vis === 'shared' ? (myUserIdForBody ?? curMember) : curMember;
      }
      if (curMember && famHandle) {
        family_enc_key_blob = null;
        family_key_handle = famHandle;
      }
    } else if (activeProvider?.keyHandle) {
      // Personal turn: send the active provider's key_handle; the server
      // reads the envelope ciphertext. enc_key_blob is null — the server
      // ignores it when null and resolves the key from its store.
      enc_key_blob = null;
      key_handle = activeProvider.keyHandle;
    }

    const ac = new AbortController();
    abortRef.current = ac;

    const finalize = (full: string) => {
      finalizedRef.current = true;
      setStreaming(null);
      setTyping(false);
      if (full) {
        appendMessage(cid, { them: true, t: { en: full, ru: full }, ts: now() });
      }
    };

    try {
      await streamChat(
        {
          persona_id: persona.id,
          convo_id: cid,
          message: text,
          enc_key_blob,
          key_handle,
          family_enc_key_blob,
          family_key_handle,
          family_id,
          visibility,
          participant_user_id,
          model: isFam
            ? famUsesPersonalKey
              ? (activeProviderForBody?.model ?? null)
              : (familyProviderForBody?.model ?? null)
            : (activeProvider?.model ?? null),
          // BYOK semantic memory: sent only when a key_handle rides this turn
          // — the server resolves the key from its envelope store and embeds
          // recall with that same per-turn key. Personal turns use the active
          // provider's embedding model; family turns use the family
          // provider's (Phase 3c), or — when `use_owner_personal_key` is on —
          // the owner's active personal provider's. Without a key_handle the
          // field is inert.
          embeddings_model: isFam
            ? family_key_handle
              ? famUsesPersonalKey
                ? (activeProviderForBody?.embeddingsModel ?? null)
                : (familyProviderForBody?.embeddings_model ?? null)
              : null
            : key_handle
              ? (activeProvider?.embeddingsModel ?? null)
              : null,
          // Builtins resolve on the server via the static registry; customs have
          // no server entry, so send their composed prompt + tone as the override.
          persona_prompt: persona.custom ? L2(persona.prompt) : null,
          persona_tone: persona.custom ? persona.tone : null,
          // Whether the companion recalls past events + atomic memories when
          // composing this turn. Extraction still runs either way; this gates
          // the salient-chains + recent-window context (see routers/llm.py).
          memory_on: memoryOn,
          // I8: idempotency key — server dedups persistence by
          // (user_id, convo_id, request_id). Reused on retry (see send()).
          request_id: requestId,
        },
        {
          onEvent: (evt) => {
            if (evt.type === 'session') {
              setTyping(false);
              setStreaming('');
            } else if (evt.type === 'token') {
              accRef.current += evt.text;
              setStreaming(accRef.current);
            } else if (evt.type === 'error') {
              finalizedRef.current = true;
              setTyping(false);
              setStreaming(null);
              // I27: mark the failed bubble so the renderer shows a Retry
              // button that re-sends the original user text verbatim.
              appendMessage(cid, {
                them: true,
                t: { en: evt.message, ru: evt.message },
                ts: now(),
                error: true,
                retryText: text,
                // I8: carry the turn's request_id so the Retry button reuses it
                // — server dedups the retried persistence by this key.
                requestId,
              });
            } else if (evt.type === 'done') {
              finalize(accRef.current);
            } else if (evt.type === 'fallback') {
              // I1: the failing provider may have streamed partial tokens
              // before the fallback fired. Drop them so the visible bubble
              // shows only the fallback provider's full reply (mirrors the
              // server resetting assistant_text on the fallback event, so
              // what the user sees and what is persisted stay in sync).
              accRef.current = '';
              setStreaming('');
              // Surface the real reason a provider failed over — otherwise a
              // BYOK call that errors (e.g. Ollama Cloud auth/model) looks
              // identical to "no key connected", which is misleading.
              const from = KIND_LABEL[evt.from_kind] ?? evt.from_kind;
              const to = KIND_LABEL[evt.to_kind] ?? evt.to_kind;
              setFallbackNotice(
                t('chat.fallback')
                  .replace('{from}', from)
                  .replace('{to}', to)
                  .replace('{reason}', evt.reason),
              );
            }
            // 'usage' is observed but doesn't drive the bubble.
          },
        },
        ac.signal,
      );
    } catch (e) {
      setTyping(false);
      setStreaming(null);
      if (accRef.current) {
        // Phase 3 #14: the stream was cut mid-flight — finalize the partial
        // bubble with a localized "[message truncated]" suffix so the user
        // sees the turn was interrupted, not silently dropped. No new wire
        // surface; this is a client-side finalize of what was already
        // streamed.
        finalize(accRef.current + L2({ en: ' [message truncated]', ru: ' [сообщение обрезано]' }));
      } else {
        finalizedRef.current = true;
        // No tokens arrived — the turn failed before the first token. Map
        // the error to a plain localized message:
        //  - a known llm/stream → NNN shape → a specific message (#13);
        //  - a real network failure (TypeError "Failed to fetch") → the
        //    "backend running on :8000" hint (#15);
        //  - anything else → a generic localized "something went wrong".
        const msg = e instanceof Error ? e.message : String(e);
        const explained = explainLlmError(msg, L2);
        const errMsg =
          explained ??
          (e instanceof TypeError && /Failed to fetch/i.test(msg)
            ? L2({
                en: 'Could not reach the companion API. Is the backend running on :8000?',
                ru: 'Не удалось связаться с API. Бэкенд запущен на :8000?',
              })
            : L2({
                en: 'Something went wrong. Try again.',
                ru: 'Что-то пошло не так. Попробуйте снова.',
              }));
        appendMessage(cid, {
          them: true,
          t: { en: errMsg, ru: errMsg },
          ts: now(),
          error: true,
          // retryText is the outer ``text`` — the original user message — NOT
          // the localized error text above (which only names the failure).
          retryText: text,
          // I8: reuse this turn's request_id on retry so the server dedups.
          requestId,
        });
      }
    } finally {
      abortRef.current = null;
      sendingRef.current = false;
      lastSendAtRef.current = Date.now();
      // K5: if the stream ended without a `done` event (server crash, proxy
      // timeout, malformed final frame), finalize was never called and the
      // composer would stay in "stop" mode forever. Recover here, preserving
      // any partial tokens. finalizedRef guards against double-finalize when
      // done/error/catch already handled it.
      if (!finalizedRef.current) {
        finalize(accRef.current);
      }
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    sendingRef.current = false;
    lastSendAtRef.current = Date.now();
    // K4: preserve whatever was already streamed instead of discarding it
    // silently. The network-error path keeps the partial with a truncated
    // suffix; a user-initiated stop should behave the same way. `finalize`
    // is scoped to send(); inline the same logic here and mark finalized so
    // the in-flight send()'s finally block doesn't double-finalize.
    const cid = convo?.id;
    const partial = accRef.current;
    accRef.current = '';
    finalizedRef.current = true;
    setTyping(false);
    if (partial && cid) {
      const full = partial + L2({ en: ' [message truncated]', ru: ' [сообщение обрезано]' });
      setStreaming(null);
      appendMessage(cid, { them: true, t: { en: full, ru: full }, ts: now() });
    } else {
      setStreaming(null);
    }
  };

  // I27: re-send the user message that produced an error bubble. The failed
  // assistant bubble is dropped first, then send() re-runs with retryText so
  // it skips re-appending the (already-present) user message. No-op while a
  // turn is in flight (send()'s own guard also rejects the double-send).
  // I8: pass the original turn's requestId so send() reuses it — the server
  // dedups the retried turn's persistence by (user_id, convo_id, request_id).
  const retry = (m: Message) => {
    if (!convo || !m.retryText || streaming !== null || sendingRef.current) return;
    removeMessage(convo.id, m);
    void send({ retryText: m.retryText, requestId: m.requestId });
  };

  // I35: delete with an undo window. Removes the row optimistically, shows a
  // toast with an Undo action, and commits the server DELETE after a grace
  // period. If the server call fails, restores the convo and surfaces an
  // error toast — the user always knows when a delete didn't reach the server.
  const onDeleteConvo = (convoId: string) => {
    const removed = useStore.getState().removeConvoFromList(convoId);
    if (!removed) return;
    let undone = false;
    const tid = window.setTimeout(() => {
      if (undone) return;
      void (async () => {
        const ok = await useStore.getState().deleteConvo(removed.convo);
        if (!ok) {
          useStore.getState().restoreConvo(removed);
          toast.error(t('chat.delete.failed'));
        }
      })();
    }, 6000);
    toast.info(t('chat.deleted.toast'), {
      action: {
        label: t('chat.undo'),
        onClick: () => {
          undone = true;
          window.clearTimeout(tid);
          useStore.getState().restoreConvo(removed);
        },
      },
    });
  };

  const placeholder = useMemo(
    () =>
      noKey && !softNudge
        ? L2({
            en: 'Chat is disabled — add a key in settings to start.',
            ru: 'Чат выключен — добавьте ключ в настройках, чтобы начать.',
          })
        : L2({
            en: `Write to ${persona.name}… (Enter to send)`,
            ru: `Напишите ${persona.name}… (Enter — отправить)`,
          }),
    [L2, persona.name, noKey, softNudge],
  );

  // Lockout banner. The same noKey state that disables the composer is
  // explained here, in plain language, with a link to the right settings
  // page for the active persona:
  //   - family persona: /family → Family key (add a key)
  //   - personal:       /onboarding (no key connected)
  //
  // There's no client-side vault to be "locked" anymore — keys live
  // server-side, so "no key" always means "no provider row". The reset
  // affordance wipes the server-side provider rows (and for the family
  // case clears the family vault metadata) so the user can re-onboard.
  type LockoutBanner = {
    msg: string;
    // Top-right CTA.
    link?: string;
    href?: string;
    // The "Reset? Wipe keys" affordance. When set, the banner renders a
    // small secondary button next to the main CTA. The reset is the
    // destructive path from lib/reset.ts — server provider delete +
    // (family only) family_salt/seed clear.
    //   - 'personal': always available; runs ``resetPersonalVault``.
    //   - 'family': owner-only at render time; runs ``resetFamilyVault``.
    resetKind?: 'personal' | 'family';
  };
  const lockoutBanner = useMemo<LockoutBanner | null>(() => {
    if (!noKey) return null;
    if (isFam) {
      if (!family) {
        return {
          msg: L2({
            en: 'No family yet. Create or join a family on /family to enable the family chat.',
            ru: 'Семьи ещё нет. Создайте или войдите в семью на /family, чтобы включить семейный чат.',
          }),
          href: '/family',
          link: L2({ en: 'Open Family →', ru: 'Открыть семью →' }),
        };
      }
      const isFamilyOwner = family.owner_user_id === principal?.user_id;
      // Flag-on + no personal key: the family rides the owner's active
      // personal key, which is missing — send the owner to /onboarding to add
      // a personal key (not to the family key tab). Members can't fix this;
      // they get the plain "ask the owner" path below.
      if (family.use_owner_personal_key) {
        if (isFamilyOwner) {
          return {
            msg: L2({
              en: 'The family uses your personal LLM key, but you have not added one yet. Add one in /onboarding to enable the family chat.',
              ru: 'Семья использует ваш личный ключ LLM, но вы его ещё не добавили. Добавьте его в /onboarding, чтобы включить семейный чат.',
            }),
            href: '/onboarding',
            link: L2({ en: 'Open Onboarding →', ru: 'Открыть онбординг →' }),
            resetKind: 'personal',
          };
        }
        return {
          msg: L2({
            en: 'The family uses the owner’s personal LLM key, but it is not connected yet. Ask the family owner to add a personal key.',
            ru: 'Семья использует личный ключ владельца, но он ещё не подключён. Попросите владельца семьи добавить личный ключ.',
          }),
          href: '/family?tab=settings&subtab=key',
          link: L2({ en: 'Open Family key →', ru: 'Открыть семейный ключ →' }),
        };
      }
      return {
        msg: L2({
          en: 'No family LLM key yet. Add one in /family → Settings → Family key to enable the family chat.',
          ru: 'Семейного ключа LLM ещё нет. Добавьте его в /family → Настройки → Семейный ключ, чтобы включить семейный чат.',
        }),
        href: '/family?tab=settings&subtab=key',
        link: L2({ en: 'Open Family key →', ru: 'Открыть семейный ключ →' }),
        // Owner-only: non-owners can't reset the family key.
        resetKind: isFamilyOwner ? 'family' : undefined,
      };
    }
    // I30: if the provider discovery itself failed with a network/5xx
    // error, we don't know whether a key exists — surfacing "go to
    // Onboarding" here would send the user to re-add a key they already
    // have. Short-circuit to a "can't reach the server" message instead,
    // and let them retry by refreshing.
    if (discoveryError) {
      return {
        msg: L2({
          en: 'Could not reach the companion API to check your key. Check your connection and refresh to retry.',
          ru: 'Не удалось связаться с API, чтобы проверить ваш ключ. Проверьте подключение и обновите страницу.',
        }),
      };
    }
    // Hosted lazy onboarding: no personal key yet, but the chain falls through
    // to the operator env fallback (trial credits / OpenRouter) — chat works,
    // so there is nothing to banner here. `softNudge` (above) keeps the
    // composer enabled; we show NO lockout banner on hosted personal scope.
    // Self-hosted keeps the hard lockout + reset path below.
    if (hosted) {
      return null;
    }
    return {
      msg: L2({
        en: 'No personal LLM key yet. Add one in Onboarding to enable the chat.',
        ru: 'Личного ключа LLM ещё нет. Добавьте его в «Начало работы», чтобы включить чат.',
      }),
      href: '/onboarding',
      link: L2({ en: 'Open Onboarding →', ru: 'Открыть «Начало работы» →' }),
      // The "Reset? Wipe keys" path. Always available here — a user who
      // wants to start over can wipe the server-side provider rows; the
      // user is left on the same /chat screen with the banner
      // re-rendering in the "no key" sub-case.
      resetKind: 'personal',
    };
  }, [noKey, isFam, family, discoveryError, principal, L2, hosted]);

  const messages = convo?.msgs ?? [];

  // I26: gate the whole chat behind the auth bootstrap so the "No key" banner
  // doesn't flash before /v1/auth/me + provider discovery resolve. The skeleton
  // matches the chat rhythm, so the swap is a paint, not a layout shift. All
  // hooks above have already run; this early return is hook-free.
  if (authLoading) return <ChatSkeleton />;

  return (
    <div
      className="chat-layout"
      data-drawer={drawerOpen ? 'on' : 'off'}
      data-sidebar={chatSidebarCollapsed ? 'off' : 'on'}
    >
      {/* Persistent conversation sidebar on desktop (OD `.convos`); on narrow
          viewports it becomes the slide-in drawer driven by `data-drawer`
          (see .chat-layout / .convos CSS). The list + new-chat affordance are
          the same markup the old slide-in `.convo-drawer` used, just relocated
          so ≥980px users always see their threads beside the canvas. */}
      <aside className="convos" aria-label={t('chat.convos')}>
        <div className="convos__head">
          <h4>{t('chat.convos')}</h4>
          <button
            type="button"
            className="icon-btn convo-close"
            aria-label={L2({ en: 'Close', ru: 'Закрыть' })}
            onClick={() => setDrawerOpen(false)}
          >
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
        <div className="convo-new">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              openNewChatPicker();
              setDrawerOpen(false);
            }}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              style={{ width: 15, height: 15 }}
            >
              <path d="M12 5v14M5 12h14" />
            </svg>
            <span>{t('nav.newchat')}</span>
          </button>
        </div>
        <div className="convo-list">
          {convos.map((c) => {
            const p = personaById(c.personaId, personas());
            return (
              // I32: row = non-interactive container holding two sibling
              // <button>s (stretched "open" + delete). Both real buttons so
              // they're keyboard-focusable; no nested-interactive hack.
              <div key={c.id} className={`ci${c.id === activeConvoId ? ' active' : ''}`}>
                <button
                  type="button"
                  className="ci-main"
                  aria-label={L2(c.title)}
                  onClick={() => {
                    useStore.getState().openConvo(c.id);
                    setDrawerOpen(false);
                  }}
                >
                  <div className="av" style={{ background: p.grad }} aria-hidden="true">
                    {p.glyph}
                  </div>
                  <div className="meta">
                    <div className="t">{L2(c.title)}</div>
                    <div className="p">{L2(c.preview)}</div>
                  </div>
                  <div className="ts" aria-hidden="true">
                    {L2(c.ts)}
                  </div>
                </button>
                <button
                  type="button"
                  className="ci-del"
                  aria-label={t('chat.delete')}
                  title={t('chat.delete')}
                  onClick={(e) => {
                    e.stopPropagation();
                    // I35: optimistic remove + Undo toast (no native confirm).
                    onDeleteConvo(c.id);
                  }}
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.6}
                  >
                    <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
                  </svg>
                </button>
              </div>
            );
          })}
        </div>
      </aside>

      <div
        className={`scrim${drawerOpen ? ' on' : ''}`}
        onClick={() => setDrawerOpen(false)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setDrawerOpen(false);
          }
        }}
      />

      <section className="chat-screen">
        <div className="chat-head">
          <button
            type="button"
            className="icon-btn convo-open"
            title={t('chat.convos')}
            aria-label={t('chat.convos')}
            aria-expanded={!chatSidebarCollapsed}
            onClick={() => {
              // One button, viewport-aware: on desktop (≥980px) the sidebar is a
              // persistent grid column, so fold/unfold it via the store. On
              // mobile it's the slide-in drawer, so toggle `drawerOpen`. Keeping
              // the branches separate stops a mobile drawer open/close from
              // polluting the desktop collapse state (and vice-versa). The 980px
              // cutoff mirrors the CSS media query that switches the layout
              // between the two-column grid and the single-column drawer.
              const desktop =
                typeof window !== 'undefined' && window.matchMedia('(min-width: 980px)').matches;
              if (desktop) {
                toggleChatSidebar();
              } else {
                setDrawerOpen((v) => !v);
              }
            }}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.6}
            >
              <path d="M3 6h18M3 12h18M3 18h18" />
            </svg>
          </button>
          <div className="av" style={{ background: persona.grad }}>
            {persona.glyph}
          </div>
          <div>
            <h3>{persona.name}</h3>
            <div className="meta">
              {(() => {
                // Headline model line. States, in order:
                //  1. A real model is selected by the user → show it.
                //  2. A provider exists but the model is empty (the user
                //     cleared the field) → fall back to the provider kind.
                //  3. No provider at all (no key, no provider row):
                //    - hosted: show the role alone — the user chats on the
                //      operator env fallback (trial credits / OpenRouter), so
                //      "no key" is misleading. There is no client-visible
                //      "no key" on hosted personal scope.
                //    - self-hosted: "no key" in plain language so the user
                //      isn't told "stand-in" without context. The old
                //      "stand-in" copy was confusing — the user took it to
                //      mean the server is offline rather than "no BYOK yet".
                const model = isFam ? familyProvider?.model : activeProvider?.model;
                const kind = isFam ? familyProvider?.kind : activeProvider?.kind;
                if (model) return `${L2(persona.role)} · ${model}`;
                if (kind) return `${L2(persona.role)} · ${kind}`;
                if (hosted) return L2(persona.role);
                return `${L2(persona.role)} · ${L2({ en: 'no key', ru: 'нет ключа' })}`;
              })()}
              {/* OD `thread__meta` second line — honest architecture flags, not
                a fabricated chain count: the persona block is injected every
                turn (deterministic, see routers/llm.py persona_block) and
                memory on/off is real client state. */}
              <span className="meta-line">
                {t('chat.meta.injected')} · {memoryOn ? t('chat.memory.on') : t('chat.memory.off')}
              </span>
            </div>
          </div>
          {isFam && family && (
            <div className="family-scope" style={{ display: 'flex', gap: 8, marginLeft: 8 }}>
              <label
                className="member-pill"
                title={L2({
                  en: 'Which member you are speaking as right now.',
                  ru: 'От чьего лица вы пишете сейчас.',
                })}
              >
                {/* Phase 3 #10: "Speaking as:" replaces the cryptic "Member:" —
                  the label now states the intent (whose voice the message is
                  in), not a noun. A helper line below the picker makes the
                  dependency explicit. */}
                <span aria-hidden style={{ opacity: 0.7 }}>
                  {L2({ en: 'Speaking as:', ru: 'От чьего лица:' })}
                </span>
                <select
                  value={activeFamilyMemberId ?? ''}
                  onChange={(e) => setActiveFamilyMemberId(e.target.value || null)}
                  aria-label={L2({
                    en: 'Active family member for solo turns',
                    ru: 'Активный участник для личных сообщений',
                  })}
                  disabled={familySessionMode === 'shared'}
                >
                  {/* Phase 3 #11: in joint mode the member pick is ignored —
                    show a neutral "— whole family —" placeholder instead of
                    the stale last solo value, so the user doesn't think a
                    specific member is still selected. The select is already
                    disabled in joint mode (above). */}
                  {familySessionMode === 'shared' && (
                    <option value="">{L2({ en: '— whole family —', ru: '— вся семья —' })}</option>
                  )}
                  {familyMembers.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.family_display_name}
                      {m.relation ? ` (${m.relation})` : ''}
                      {m.user_id === family.owner_user_id ? ' ★' : ''}
                    </option>
                  ))}
                </select>
                <span className="caret" aria-hidden>
                  ▾
                </span>
              </label>
              <div className="seg" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={familySessionMode === 'private'}
                  className={familySessionMode === 'private' ? 'on' : ''}
                  onClick={() => {
                    setFamilySessionMode('private');
                    // Align the active convo with the mode: if we're currently
                    // on the shared joint convo, move to a fresh solo 1:1. If
                    // already on a solo convo, leave it. (setFamilySessionMode
                    // is a synchronous zustand set, so newChat sees the new
                    // mode when it mints.)
                    if (convo?.id.startsWith('fam-joint-')) newChat('fam');
                  }}
                  title={L2({ en: 'Solo 1:1', ru: 'Личная 1:1' })}
                >
                  {L2({ en: 'Solo 1:1', ru: 'Соло 1:1' })}
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={familySessionMode === 'shared'}
                  className={familySessionMode === 'shared' ? 'on' : ''}
                  onClick={() => {
                    setFamilySessionMode('shared');
                    // Joint = the one shared family convo. newChat mints the
                    // deterministic fam-joint-{familyId} id (create-or-reuse)
                    // so every member lands on the same thread.
                    newChat('fam');
                  }}
                  title={L2({ en: 'Joint with the whole family', ru: 'Совместная со всей семьёй' })}
                >
                  {L2({ en: 'Joint', ru: 'Совместно' })}
                </button>
              </div>
              {/* Phase 3 #16: keep family management reachable in the chat
                header — on mobile the rail is collapsed so /family is
                otherwise a long way away. Small ghost link, no chrome. */}
              <Link
                href="/family"
                className="btn btn-sm btn-ghost"
                data-family-settings-link
                style={{ whiteSpace: 'nowrap' }}
              >
                {L2({ en: 'Family settings', ru: 'Семейные настройки' })}
              </Link>
            </div>
          )}
          <div className="right">
            <button
              type="button"
              className={`btn btn-sm btn-ghost${memoryOn ? ' memory-on' : ''}`}
              title={t('chat.memory.tip')}
              aria-pressed={memoryOn}
              onClick={toggleMemoryOn}
            >
              {memoryOn ? t('chat.memory.on') : t('chat.memory.off')}
            </button>
          </div>
        </div>

        {fallbackNotice && (
          <div
            className="chat-notice"
            style={{ padding: '8px 16px', borderBottom: '1px solid var(--border)' }}
          >
            <div className="alt-line" style={{ color: 'var(--muted, #8a8a98)' }}>
              <span>{fallbackNotice}</span>
            </div>
          </div>
        )}

        {/* Phase 3 #10/#11/#12: plain-language helpers for the family in-chat
          session. One line under the header explaining the solo/joint
          semantics (#12), plus a context-sensitive hint for the member pick:
          in solo mode whose-voice it is (#10), in joint mode that the pick is
          ignored (#11). Kept muted so they read as guidance, not chrome. */}
        {isFam && family && (
          <div
            className="family-session-hint"
            data-family-session-hint
            style={{ padding: '6px 16px', borderBottom: '1px solid var(--border)' }}
          >
            <div className="alt-line" style={{ color: 'var(--muted, #8a8a98)', fontSize: 12 }}>
              <span>
                {familySessionMode === 'shared'
                  ? L2({
                      en: 'Joint mode — the whole family; the member pick is ignored.',
                      ru: 'Совместный режим — вся семья; выбор участника игнорируется.',
                    })
                  : L2({
                      en: 'Which member you are speaking as right now.',
                      ru: 'От чьего лица вы пишете сейчас.',
                    })}
              </span>
              <span style={{ marginInline: 8 }}>·</span>
              <span>
                {L2({
                  en: 'Solo 1:1 — private recall for this member. Joint — shared family recall.',
                  ru: 'Соло 1:1 — личные воспоминания участника. Совместно — общие семейные воспоминания.',
                })}
              </span>
            </div>
          </div>
        )}

        <div className="stream" ref={streamRef} onScroll={onStreamScroll}>
          {messages.map((m, i) => {
            // Joint (shared family) thread: attribute each bubble to its
            // author. Other members' user messages align LEFT as `.msg.other`
            // (so they don't read as the viewer's own); the local user stays
            // right `.msg.me`; the therapist stays left `.msg.them`. Outside a
            // joint convo this branch is skipped entirely — personal / solo
            // / non-fam chats render exactly as before, with no caption.
            const joint = convo ? convoFamilyVisibility(convo.id) === 'shared' : false;
            const myUserId = principal?.user_id ?? null;
            const speaker =
              joint && m.speakerUserId
                ? familyMembers.find((fm) => fm.user_id === m.speakerUserId)
                : undefined;
            const isOtherMember = joint && !m.them && !!speaker && speaker.user_id !== myUserId;
            const bubbleClass = m.them ? 'them' : isOtherMember ? 'other' : 'me';
            // Caption: the therapist persona's name on assistant bubbles, the
            // speaking member's display name on user bubbles. Empty (no
            // caption rendered) when there's no speaker to attribute (e.g. an
            // un-tagged legacy message or a non-family turn outside joint).
            const authorLabel = joint
              ? m.them
                ? L2(persona.role)
                : speaker
                  ? speaker.family_display_name
                  : ''
              : '';
            const memberColor = speaker?.color;
            return (
              <div
                key={i}
                className={`msg ${bubbleClass}`}
                style={
                  isOtherMember && memberColor
                    ? ({ '--member-color': memberColor } as CSSProperties)
                    : undefined
                }
              >
                {joint && authorLabel && (
                  <div
                    className="msg-author"
                    style={isOtherMember && memberColor ? { color: memberColor } : undefined}
                  >
                    {authorLabel}
                  </div>
                )}
                <div className="body">
                  {m.them ? <Markdown>{L2(m.t)}</Markdown> : L2(m.t)}
                  {m.them && (
                    <div className="msg-them-actions">
                      <button
                        type="button"
                        className="mini"
                        onClick={() => {
                          const text = stripMarkdown(L2(m.t));
                          if (navigator.clipboard?.writeText) {
                            void navigator.clipboard.writeText(text).then(
                              () => toast.info(t('chat.copied')),
                              () => {},
                            );
                          }
                        }}
                      >
                        <svg
                          aria-hidden="true"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth={1.8}
                        >
                          <rect x="9" y="9" width="11" height="11" rx="2" />
                          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
                        </svg>
                        {t('chat.copy')}
                      </button>
                    </div>
                  )}
                  {!m.them && !isOtherMember && (
                    <div className="msg-me-actions">
                      <button
                        type="button"
                        className="mini"
                        onClick={() => {
                          // Seed the journal composer with this message and jump to
                          // /journal, where the user edits/saves it as a diary entry.
                          // The client can't see the server event id (it doesn't
                          // round-trip per message), so source_event_id stays null;
                          // the convo id carries the provenance link.
                          setJournalSeed({
                            personaId: persona.id,
                            convoId: convo?.id ?? '',
                            eventId: null,
                            text: L2(m.t),
                          });
                          router.push('/journal');
                        }}
                      >
                        <svg
                          aria-hidden="true"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth={1.8}
                        >
                          <path d="M5 4h9a2 2 0 0 1 2 2v14a1 1 0 0 1-1 1H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1z" />
                          <path d="M15 7l4-2-1.5 5.5L15 13z" />
                        </svg>
                        {t('journal.chat.save')}
                      </button>
                    </div>
                  )}
                  {m.error && (
                    <div className="msg-them-actions">
                      <button
                        type="button"
                        className="mini retry"
                        aria-label={L2({ en: 'Retry sending', ru: 'Отправить снова' })}
                        onClick={() => retry(m)}
                      >
                        <svg
                          aria-hidden="true"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth={1.8}
                        >
                          <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                          <path d="M21 3v5h-5" />
                          <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
                          <path d="M3 21v-5h5" />
                        </svg>
                        {L2({ en: 'Retry', ru: 'Повторить' })}
                      </button>
                    </div>
                  )}
                  <div className="ts">{m.ts}</div>
                </div>
              </div>
            );
          })}
          {typing && (
            <output className="msg them typing">
              <div className="thinking-row">
                <span className="typing-dots">
                  <span />
                  <span />
                  <span />
                </span>
                <span className="thinking-label">{t('chat.thinking')}</span>
              </div>
              <div className="shimmer-line" />
              <div className="shimmer-line short" />
            </output>
          )}
          {streaming !== null && (
            <div className="msg them">
              <div className="body">
                <Markdown>{streaming}</Markdown>
                <div className="ts">{now()}</div>
              </div>
            </div>
          )}
        </div>

        <div className="chat-locked-banner-wrap">
          {lockoutBanner && (
            <div
              className="chat-locked-banner"
              // role=alert makes screen readers announce the lockout
              // immediately on mount. The visible link below is the
              // keyboard-actionable target.
              role="alert"
            >
              <span>{lockoutBanner.msg}</span>
              {lockoutBanner.href && (
                <Link href={lockoutBanner.href} style={{ marginLeft: 'auto' }}>
                  {lockoutBanner.link}
                </Link>
              )}
              {lockoutBanner.resetKind && !resetOpen && (
                // "Reset? Wipe keys" — a secondary destructive affordance.
                // The personal case is always shown; the family case is
                // owner-only (the lockoutBanner memo already filters
                // ``resetKind`` for the family owner check). Opening it
                // shows a typed-confirmation step below — see the
                // ``resetOpen`` branch below.
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  onClick={() => {
                    setResetOpen(true);
                    setResetTyped('');
                    setResetErr(null);
                  }}
                  style={{ marginLeft: 8 }}
                >
                  {L2({ en: 'Reset? Wipe keys', ru: 'Сбросить? Стереть ключи' })}
                </button>
              )}
              {resetOpen && lockoutBanner.resetKind && (
                // Inline reset confirm. The user must type a literal
                // phrase to enable the destructive button — for the
                // personal case the phrase is the family-name-style
                // "RESET"; for the family case it is the family name
                // verbatim (matching the /family reset confirm).
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void doReset();
                  }}
                  className="chat-unlock-form"
                >
                  {lockoutBanner.resetKind === 'family' ? (
                    <>
                      <div className="help" style={{ flexBasis: '100%' }}>
                        {t('fam.vault.reset.hint')}
                      </div>
                      <div className="help" style={{ flexBasis: '100%' }}>
                        {t('fam.vault.reset.confirm.phrase')}
                      </div>
                      <input
                        className="input"
                        value={resetTyped}
                        onChange={(e) => setResetTyped(e.target.value)}
                        placeholder={family?.name ?? ''}
                        aria-label={t('fam.vault.reset.confirm.phrase')}
                        disabled={resetting}
                      />
                      <button
                        type="submit"
                        className="btn btn-sm"
                        disabled={
                          resetting ||
                          resetTyped.trim() !== (family?.name ?? '').trim() ||
                          !(family?.name ?? '').length
                        }
                      >
                        {t('fam.vault.reset.confirm.yes')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        onClick={() => {
                          setResetOpen(false);
                          setResetTyped('');
                          setResetErr(null);
                        }}
                        disabled={resetting}
                      >
                        {t('fam.vault.reset.confirm.no')}
                      </button>
                    </>
                  ) : (
                    <>
                      <div className="help" style={{ flexBasis: '100%' }}>
                        {L2({
                          en: 'Type RESET to wipe all connected keys on the server. You will need to re-add a key to chat again.',
                          ru: 'Введите RESET, чтобы стереть все подключённые ключи на сервере. Чтобы продолжить чат, нужно будет заново добавить ключ.',
                        })}
                      </div>
                      <input
                        className="input"
                        value={resetTyped}
                        onChange={(e) => setResetTyped(e.target.value)}
                        placeholder="RESET"
                        aria-label={L2({ en: 'Type RESET', ru: 'Введите RESET' })}
                        disabled={resetting}
                      />
                      <button
                        type="submit"
                        className="btn btn-sm"
                        disabled={resetting || resetTyped.trim() !== 'RESET'}
                      >
                        {L2({ en: 'Wipe & reset', ru: 'Стереть и сбросить' })}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        onClick={() => {
                          setResetOpen(false);
                          setResetTyped('');
                          setResetErr(null);
                        }}
                        disabled={resetting}
                      >
                        {L2({ en: 'Cancel', ru: 'Отмена' })}
                      </button>
                    </>
                  )}
                  {resetErr && (
                    <span
                      className="help"
                      style={{ color: 'var(--warn, #d4a23a)', flexBasis: '100%' }}
                    >
                      {resetErr}
                    </span>
                  )}
                </form>
              )}
            </div>
          )}
        </div>

        {/* OD `keyind` — an honest read of the active key source sitting just
          above the composer. BYOK keys live server-side
          (envelope-encrypted); the opaque key_handle is NOT the key, so we
          never render a fake `sk-•••3a2f` fingerprint (that would be
          performative). We surface the provider kind + connected/no-key + a
          change link. Hidden on hosted: a hosted user chats on the operator
          env fallback (trial credits / OpenRouter) and has no BYOK key to
          speak of, so the "BYOK / no key / change" bar is misleading there. */}
        {!hosted && (
          <div className="chat-keyind" aria-live="polite">
            <span className="keyind">
              <span className="k">BYOK</span>
              {noKey ? (
                <span className="v v-none">{t('chat.keyind.none')}</span>
              ) : (
                <span className="v">
                  {KIND_LABEL[
                    isFam ? (familyProvider?.kind ?? '') : (activeProvider?.kind ?? '')
                  ] ?? L2({ en: 'key', ru: 'ключ' })}{' '}
                  · {t('chat.keyind.connected')}
                </span>
              )}
              <Link href={isFam ? '/family?tab=settings&subtab=key' : '/onboarding'}>
                {t('chat.keyind.change')}
              </Link>
            </span>
          </div>
        )}

        <div className="composer">
          <textarea
            ref={taRef}
            className="input"
            rows={1}
            placeholder={placeholder}
            value={input}
            onChange={(e) => {
              // Block keystrokes when locked — the textarea is disabled, but
              // some browsers still let focus/IME fire onChange in edge cases.
              // We additionally ignore the event so no character is ever
              // staged. The user sees the placeholder and the banner above.
              // softNudge (hosted, no personal key) keeps the composer open.
              if (noKey && !softNudge) return;
              setInput(e.target.value);
              autosize();
            }}
            onKeyDown={(e) => {
              if (noKey && !softNudge) {
                // Suppress Enter-to-send. Otherwise a stuck focus would
                // trigger send() on a still-empty locked composer.
                if (e.key === 'Enter') e.preventDefault();
                return;
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            disabled={noKey && !softNudge}
            aria-disabled={noKey && !softNudge}
          />
          <button
            type="button"
            className="btn btn-primary send"
            onClick={streaming !== null ? stop : () => send()}
            title={streaming !== null ? t('chat.stop') : t('nav.newchat')}
            aria-label={streaming !== null ? t('chat.stop') : t('nav.newchat')}
            disabled={noKey && !softNudge && streaming === null}
          >
            {streaming !== null ? (
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            ) : (
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            )}
          </button>
        </div>
      </section>
    </div>
  );
}
