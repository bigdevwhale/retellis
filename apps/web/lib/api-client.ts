'use client';

// Thin typed wrapper over the Retellis API. Typed by @ai-companion/contracts
// where it matters (provider metadata). The BYOK key itself never travels here
// — it is sealed to the server inside lib/vault.ts and attached per request.
//
// Identity: every call sends `credentials: 'include'` so the verified session
// cookie (HttpOnly + Secure + SameSite=Lax, set by /v1/auth/*) rides along. The
// supported deploy is single-origin — Caddy in production proxies /v1 → the API
// on one origin, and next.config.ts `rewrites()` does the same for `pnpm dev` —
// so the cookie is first-party and SameSite=Lax suffices. API_URL defaults to
// same-origin relative (''); set NEXT_PUBLIC_API_URL only as a cross-origin dev
// escape hatch (then the API must allow credentials + a non-wildcard origin).

import type {
  AuthConfig,
  CheckoutRequest,
  CheckoutSession,
  FamilyTherapistPrompt,
  FamilyTherapistPromptSet,
  Messenger,
  Plan,
  PortalSession,
  Principal,
  SessionInfo,
  Subscription,
  TelegramInitResponse,
} from '@ai-companion/contracts';
import type { ProviderKind } from './fixtures';

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? '').replace(/\/$/, '');

// I30: a typed API error so callers can tell a *network* failure (no response
// at all — DNS/CORS/offline/timeout before connect) from an HTTP status. The
// lockout discovery (ChatScreen) and the auth boot (auth.tsx) both need this:
//   - status === null  → the request never reached the server. Retried once
//     for idempotent GETs (see jsonFetch). Callers should surface "can't reach
//     the server" rather than "no key connected".
//   - status === 4xx/5xx → the server answered. 401 means "no session"; a 5xx
//     means "server error, don't make assumptions about auth/key state".
export class ApiError extends Error {
  readonly path: string;
  readonly status: number | null;
  constructor(path: string, status: number | null, message?: string) {
    super(message ?? (status === null ? `${path} → network error` : `${path} → ${status}`));
    this.name = 'ApiError';
    this.path = path;
    this.status = status;
  }
}

/** True for a network failure (no response) or a server-side 5xx — both mean
 * "we can't trust the result, don't derive banner/auth state from it". */
export function isTransientOrNetworkError(e: unknown): boolean {
  return e instanceof ApiError && (e.status === null || e.status >= 500);
}

const GET_RETRY_DELAY_MS = 600;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export type HealthResponse = {
  status: string;
  langfuse: string;
  ecdh_pub: string;
};

export type ProviderRecord = {
  id: string;
  user_id: string;
  kind: ProviderKind;
  label: string;
  base_url: string | null;
  key_handle: string | null;
  model: string | null;
  // Embedding model for semantic memory recall (e.g. "text-embedding-3-small").
  // null = semantic memory off for this provider. Plain metadata (a model id,
  // never a key) — the recall embedding call reuses the same per-turn
  // ECDH-sealed BYOK key as the chat call.
  embeddings_model: string | null;
  // Zero-knowledge at-rest backup (base64 salt||nonce||ct). Present when the
  // client opted into sync; null for older rows / mock provider. Used only to
  // restore the vault after a browser cache wipe.
  enc_blob: string | null;
};

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET';
  // Only GET is retried on a network failure — POST/PATCH/DELETE are not
  // idempotent and a retry could double-apply the side effect.
  const idempotent = method === 'GET';
  const url = `${API_URL}${path}`;
  const baseInit: RequestInit = {
    ...init,
    // Carry the verified session cookie. Same-origin in the supported deploy, so
    // this is equivalent to same-origin; `include` keeps it correct if a
    // cross-origin NEXT_PUBLIC_API_URL is configured (provided CORS allows it).
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  };

  let res: Response;
  try {
    res = await fetch(url, baseInit);
  } catch (firstErr) {
    // Network failure: no response at all. For idempotent GETs, retry once
    // after a short backoff — a flaky connection or transient DNS hiccup
    // shouldn't surface as a hard error to the user. Mutations are never
    // retried (not idempotent), and the caller gets the ApiError(null) to
    // surface "can't reach the server" rather than a misleading empty result.
    if (!idempotent) {
      throw new ApiError(path, null, firstErr instanceof Error ? firstErr.message : undefined);
    }
    await sleep(GET_RETRY_DELAY_MS);
    try {
      res = await fetch(url, baseInit);
    } catch (secondErr) {
      throw new ApiError(path, null, secondErr instanceof Error ? secondErr.message : undefined);
    }
  }

  if (!res.ok) {
    throw new ApiError(path, res.status);
  }
  // 204 No Content has no body to parse — return undefined for void endpoints
  // (DELETE /v1/providers/:id, DELETE /v1/memory/shares). Parsing would throw
  // on the empty body in the browser.
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return jsonFetch<HealthResponse>('/v1/health');
}

export function listProviders(): Promise<ProviderRecord[]> {
  return jsonFetch<ProviderRecord[]>('/v1/providers');
}

export function createProvider(body: {
  kind: ProviderKind;
  label: string;
  base_url?: string | null;
  key_handle?: string | null;
  model?: string | null;
  embeddings_model?: string | null;
  // Legacy at-rest backup column — now unused. Send null.
  enc_blob?: string | null;
  // One-time ECDH-sealed plaintext key (sealed to the server's session
  // pubkey via libsodium crypto_box_seal). The server opens it with its
  // session private key and envelope-encrypts it at rest under
  // MESSENGER_TOKEN_DEK. 503 if the server has no envelope DEK.
  enc_key_blob?: string | null;
}): Promise<ProviderRecord> {
  return jsonFetch<ProviderRecord>('/v1/providers', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function deleteProvider(id: string): Promise<void> {
  return jsonFetch<void>(`/v1/providers/${id}`, { method: 'DELETE' });
}

// Partial update of a provider — only ``label`` / ``model`` / ``base_url`` /
// ``embeddings_model`` may change here; ``key_handle`` and the server-side
// envelope ciphertext are immutable (rotation = delete + re-add, which
// re-seals the key). Omit a key to leave the column alone; pass ``null``
// explicitly to clear ``base_url`` / ``model`` / ``embeddings_model``.
// ``label`` is required-non-null — absent falls back to the existing value.
export function updateProvider(
  id: string,
  body: {
    label?: string;
    model?: string | null;
    base_url?: string | null;
    embeddings_model?: string | null;
  },
): Promise<ProviderRecord> {
  return jsonFetch<ProviderRecord>(`/v1/providers/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

// --- Memory (event-chain + recall) ---

export type EventRecord = {
  id: string;
  user_id: string;
  persona_id: string;
  prev_event_id: string | null;
  role: 'user' | 'assistant' | 'system';
  content: string;
  salience: number;
  // Multi-dimensional salience (Phase 1b). Auto-classified — the UI must
  // present these as classifier output, never as claims about feelings.
  short_term_salience: number;
  emotional_intensity: number;
  emotion_tags: string[];
  // Family scope (None for personal/non-family). ``participant_user_id`` tags
  // the speaking member on a user-role event (None on assistant events); the
  // joint-thread renderer attributes each bubble to its author. See Event in
  // packages/contracts — the wire carries family_id/visibility too, but the
  // UI projection only needs the speaker id.
  participant_user_id: string | null;
};

export type EventChainRecord = {
  events: EventRecord[];
  salience_sum: number;
};

// K6: a conversation-list projection derived server-side from the event chain
// (grouped by convo_id). The drawer hydrates from GET /v1/conversations so the
// list survives a refresh — the server, not fixtures, is the source of truth.
// title/preview are single strings (truncated server-side); the UI maps them
// into its Localized {en, ru} shape by reusing the same string for both.
export type ConversationSummaryRecord = {
  convo_id: string;
  persona_id: string;
  title: string;
  preview: string;
  event_count: number;
  created_at: string;
  last_activity: string;
  family_id: string | null;
  visibility: 'private' | 'shared';
};

// An atomic, LLM-derived memory — the display unit of the /memory page.
export type MemoryRecord = {
  id: string;
  user_id: string;
  persona_id: string;
  content: string;
  tags: string[];
  salience: number;
  source_event_ids: string[];
  status: 'active' | 'superseded';
  created_at: string;
  updated_at: string;
};

export function listEvents(
  personaId: string,
  limit = 50,
  opts?: {
    convoId?: string;
    familyFilter?: {
      familyId: string;
      visibility: 'private' | 'shared';
      participantUserId: string;
    };
  },
): Promise<EventRecord[]> {
  const q = new URLSearchParams({ persona_id: personaId, limit: String(limit) });
  if (opts?.convoId) q.set('convo_id', opts.convoId);
  if (opts?.familyFilter) {
    q.set('family_id', opts.familyFilter.familyId);
    q.set('visibility', opts.familyFilter.visibility);
    q.set('participant_user_id', opts.familyFilter.participantUserId);
  }
  return jsonFetch<EventRecord[]>(`/v1/memory?${q.toString()}`);
}

// K6: the conversation-list projection. ``personaId`` optional — omit to list
// across all the user's personas. ``before`` is a backward cursor (ISO 8601
// last_activity) for pagination; the server orders last_activity desc.
// ``familyFilter`` scopes to a family session — pass ``visibility: 'shared'``
// to surface the joint family convo (which lives under other members' user_ids
// too, so it would not appear in the personal-only default call).
export function listConversations(
  personaId?: string,
  before?: string,
  limit = 50,
  familyFilter?: {
    familyId: string;
    visibility: 'private' | 'shared';
    participantUserId: string;
  },
): Promise<ConversationSummaryRecord[]> {
  const q = new URLSearchParams({ limit: String(limit) });
  if (personaId) q.set('persona_id', personaId);
  if (before) q.set('before', before);
  if (familyFilter) {
    q.set('family_id', familyFilter.familyId);
    q.set('visibility', familyFilter.visibility);
    q.set('participant_user_id', familyFilter.participantUserId);
  }
  return jsonFetch<ConversationSummaryRecord[]>(`/v1/conversations?${q.toString()}`);
}

export function listMemories(
  personaId: string,
  familyFilter?: { familyId: string; visibility: 'private' | 'shared'; participantUserId: string },
): Promise<MemoryRecord[]> {
  const q = new URLSearchParams({ persona_id: personaId });
  if (familyFilter) {
    q.set('family_id', familyFilter.familyId);
    q.set('visibility', familyFilter.visibility);
    q.set('participant_user_id', familyFilter.participantUserId);
  }
  return jsonFetch<MemoryRecord[]>(`/v1/memories?${q.toString()}`);
}

export function recallMemory(personaId: string, query: string, k = 3): Promise<EventChainRecord[]> {
  return jsonFetch<EventChainRecord[]>('/v1/memory/recall', {
    method: 'POST',
    body: JSON.stringify({ persona_id: personaId, query, k }),
  });
}

// A cross-persona live memory link — a reference, not a copy. Donor-initiated:
// donorPersonaId shares its memory INTO receiverPersonaId. The donor's rows stay
// owned by the donor; the receiver's read paths union them while the link exists.
export type MemoryShareRecord = {
  id: string;
  user_id: string;
  donor_persona_id: string;
  receiver_persona_id: string;
  created_at: string;
};

export function listMemoryShares(donorPersonaId: string): Promise<MemoryShareRecord[]> {
  const q = new URLSearchParams({ donor_persona_id: donorPersonaId });
  return jsonFetch<MemoryShareRecord[]>(`/v1/memory/shares?${q.toString()}`);
}

export function addMemoryShare(
  donorPersonaId: string,
  receiverPersonaId: string,
): Promise<MemoryShareRecord> {
  return jsonFetch<MemoryShareRecord>('/v1/memory/shares', {
    method: 'POST',
    body: JSON.stringify({
      donor_persona_id: donorPersonaId,
      receiver_persona_id: receiverPersonaId,
    }),
  });
}

export function removeMemoryShare(
  donorPersonaId: string,
  receiverPersonaId: string,
): Promise<void> {
  const q = new URLSearchParams({
    donor_persona_id: donorPersonaId,
    receiver_persona_id: receiverPersonaId,
  });
  return jsonFetch<void>(`/v1/memory/shares?${q.toString()}`, { method: 'DELETE' });
}

// --- Memory & dialog reset ---
//
// deleteConvoEvents: the server half of "delete conversation" — removes one
// thread's raw message events. Derived memories persist (un-learning is
// wipePersonaMemory). wipePersonaMemory: un-learns a persona's events +
// memories + its OUTGOING donor shares; incoming shares from other personas
// are donor-owned and stay. Both idempotent (204 on missing).

export function deleteConvoEvents(personaId: string, convoId: string): Promise<void> {
  const q = new URLSearchParams({ persona_id: personaId, convo_id: convoId });
  return jsonFetch<void>(`/v1/memory/convo?${q.toString()}`, { method: 'DELETE' });
}

export function wipePersonaMemory(personaId: string): Promise<void> {
  const q = new URLSearchParams({ persona_id: personaId });
  return jsonFetch<void>(`/v1/memory?${q.toString()}`, { method: 'DELETE' });
}

// --- Routing & budget ---

export type RoutingNode = {
  kind: string; // ProviderKind or 'mock'
  model: string;
  base_url: string | null;
  status: 'healthy' | 'standby' | 'unavailable';
};

export type ProviderSummary = {
  kind: string;
  model: string;
  requests: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  status: 'healthy' | 'standby' | 'unavailable';
};

export type RoutingState = {
  chain: RoutingNode[];
  monthly_budget_usd: number;
  spent_usd: number;
  remaining_usd: number;
  fallback_last_turn: string | null;
  pct: number;
  warn: boolean;
  hard_stop: boolean;
  per_provider: ProviderSummary[];
  langfuse_url: string | null;
};

export function getRouting(): Promise<RoutingState> {
  return jsonFetch<RoutingState>('/v1/routing');
}

// --- Journal (the user-authored diary, separate from the chat event chain) ---
//
// Entries are written directly by the user OR seeded from a chat message via
// "Save to journal" (which copies the message text and links source_convo_id).
// mood + tags are authored by the user — the journal surfaces them as-is and
// never generates affective claims ("disclose, don't perform"). No key material
// and no LLM call touches this surface.

export type JournalEntryRecord = {
  id: string;
  user_id: string;
  persona_id: string;
  title: string | null;
  body: string;
  mood: string | null;
  tags: string[];
  salience: number;
  source_convo_id: string | null;
  source_event_id: string | null;
  created_at: string;
  updated_at: string;
};

export type JournalListParams = {
  personaId?: string;
  q?: string;
  tag?: string;
  mood?: string;
  from?: string; // ISO datetime, lower bound on created_at
  to?: string; // ISO datetime, upper bound on created_at
  limit?: number;
  offset?: number;
};

export function listJournalEntries(params: JournalListParams = {}): Promise<JournalEntryRecord[]> {
  const q = new URLSearchParams();
  if (params.personaId) q.set('persona_id', params.personaId);
  if (params.q) q.set('q', params.q);
  if (params.tag) q.set('tag', params.tag);
  if (params.mood) q.set('mood', params.mood);
  if (params.from) q.set('from', params.from);
  if (params.to) q.set('to', params.to);
  if (params.limit !== undefined) q.set('limit', String(params.limit));
  if (params.offset !== undefined) q.set('offset', String(params.offset));
  const qs = q.toString();
  return jsonFetch<JournalEntryRecord[]>(`/v1/journal${qs ? `?${qs}` : ''}`);
}

// Distinct tag cloud for the /journal sidebar. Same filter shape as
// ``listJournalEntries`` minus ``q``/``tag``/``limit``/``offset`` (an
// aggregate, not a list). The server returns ``{ tags: string[] }``; we
// unwrap to ``string[]`` so the UI does not have to know the envelope.
export type JournalTagListParams = {
  personaId?: string;
  mood?: string;
  from?: string;
  to?: string;
  familyId?: string;
};
export function listJournalTags(params: JournalTagListParams = {}): Promise<string[]> {
  const q = new URLSearchParams();
  if (params.personaId) q.set('persona_id', params.personaId);
  if (params.mood) q.set('mood', params.mood);
  if (params.from) q.set('from', params.from);
  if (params.to) q.set('to', params.to);
  if (params.familyId) q.set('family_id', params.familyId);
  const qs = q.toString();
  return jsonFetch<{ tags: string[] }>(`/v1/journal/tags${qs ? `?${qs}` : ''}`).then((r) => r.tags);
}

export function createJournalEntry(body: {
  persona_id: string;
  body: string;
  title?: string | null;
  mood?: string | null;
  tags?: string[];
  salience?: number;
  source_convo_id?: string | null;
  source_event_id?: string | null;
}): Promise<JournalEntryRecord> {
  return jsonFetch<JournalEntryRecord>('/v1/journal', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// Partial update — only supplied keys mutate (PATCH semantics). Omit a key to
// leave the column alone; pass ``null`` explicitly to clear the nullable
// ``title`` / ``mood``. ``body`` cannot be emptied (the router rejects that
// with 422). ``salience`` is not patchable here — it stays as authored.
export function updateJournalEntry(
  id: string,
  body: { title?: string | null; body?: string; mood?: string | null; tags?: string[] },
): Promise<JournalEntryRecord> {
  return jsonFetch<JournalEntryRecord>(`/v1/journal/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function deleteJournalEntry(id: string): Promise<void> {
  return jsonFetch<void>(`/v1/journal/${id}`, { method: 'DELETE' });
}

// --- Auth & deployment mode ---
//
// The auth endpoints are all FastAPI-owned (see apps/api auth/router.py). The
// web never verifies tokens or holds a session secret — it only drives login
// flows and reads the resulting Principal from /v1/auth/me. /v1/config is
// public (no cookie) and tells the login/onboarding/nav which screens to render
// for the deployment mode; per-user entitlements (plan, credits) ride on the
// Principal. BYOK keys are sealed once in transit and stored envelope-encrypted
// on the server — they are never sent with auth calls.

/** Public — the deployment mode + enabled auth backends + feature flags. */
export function getAuthConfig(): Promise<AuthConfig> {
  return jsonFetch<AuthConfig>('/v1/config');
}

/** The verified Principal for the current session cookie, or 401 if absent.
 *
 * I31: 401 → null (genuinely no session → the gate may redirect). Any other
 * failure (network error or 5xx) throws an ``ApiError`` so the auth gate can
 * tell "not authenticated" apart from "we couldn't reach the server" — a 5xx
 * during boot must NOT redirect to /login against a valid session. */
export async function getMe(): Promise<Principal | null> {
  const url = `${API_URL}/v1/auth/me`;
  const baseInit: RequestInit = { credentials: 'include' };
  let res: Response;
  try {
    res = await fetch(url, baseInit);
  } catch (firstErr) {
    // Retry once on a network failure — boot happens once, and a single
    // transient blip shouldn't push the user into the error state.
    await sleep(GET_RETRY_DELAY_MS);
    try {
      res = await fetch(url, baseInit);
    } catch (secondErr) {
      throw new ApiError(
        '/v1/auth/me',
        null,
        secondErr instanceof Error ? secondErr.message : undefined,
      );
    }
  }
  if (res.status === 401) return null;
  if (!res.ok) throw new ApiError('/v1/auth/me', res.status);
  return (await res.json()) as Principal;
}

export function localSignup(body: {
  email: string;
  password: string;
  display_name?: string;
}): Promise<Principal> {
  return jsonFetch<Principal>('/v1/auth/signup', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function localLogin(body: { email: string; password: string }): Promise<Principal> {
  return jsonFetch<Principal>('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function magicLinkRequest(email: string): Promise<void> {
  return jsonFetch<void>('/v1/auth/magiclink', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

/** Verify a magic-link token from the email URL; sets the session cookie. */
export async function magicLinkVerify(token: string): Promise<boolean> {
  // The server 303-redirects on success; for an XHR caller we only care that the
  // cookie got set, so follow redirects and check the final status.
  const res = await fetch(
    `${API_URL}/v1/auth/magiclink/verify?token=${encodeURIComponent(token)}`,
    {
      credentials: 'include',
      redirect: 'follow',
    },
  );
  return res.ok;
}

export function logout(): Promise<void> {
  return jsonFetch<void>('/v1/auth/logout', { method: 'POST' });
}

/**
 * Re-send the email-verification link. Non-enumerating: the server acks
 * `{ok:true}` for any email (unknown / already-verified / non-local all
 * no-op server-side), so this never reveals account state. 404 when the
 * deployment has FEATURE_EMAIL_VERIFICATION off (the UI only offers this
 * when `config.features.email_verification` is on, so a 404 here is a
 * config drift / stale-session case — surface it as a generic error).
 */
export function resendVerificationEmail(email: string): Promise<{ ok: boolean }> {
  return jsonFetch<{ ok: boolean }>('/v1/auth/verify-email/resend', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

// --- Session management (M2: active devices) ---
//
// The session token (cookie value, a secret) is never surfaced — the list keys
// off the opaque surrogate id. ``current`` marks the session whose cookie is on
// this request; it cannot be revoked from its own card (use logout()). A 404 on
// revoke means the id was wrong / already revoked / belonged to another user —
// the server does not distinguish (cross-tenant → 404, not 403).

export type SessionInfoRecord = SessionInfo;

export function listSessions(): Promise<SessionInfoRecord[]> {
  return jsonFetch<SessionInfoRecord[]>('/v1/auth/sessions');
}

export function revokeSession(id: string): Promise<void> {
  return jsonFetch<void>(`/v1/auth/sessions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

/** Sign out everywhere EXCEPT the current session. Returns the count revoked. */
export async function revokeOtherSessions(): Promise<number> {
  // jsonFetch treats 204 as void; this endpoint returns 200 + {revoked: n}.
  const res = await fetch(`${API_URL}/v1/auth/sessions`, {
    method: 'DELETE',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new ApiError('/v1/auth/sessions', res.status);
  const body = (await res.json()) as { revoked: number };
  return body.revoked;
}

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

// --- Family (multi-member, real per-user accounts) ---
//
// Family CRUD + invites + members + family providers. The family API key (the
// owner's) is sealed once in transit and stored envelope-encrypted on the
// server. There is no client-side family vault; only the owner can add or
// change the family key.

export type FamilyRole = 'owner' | 'member';

export type FamilyRecord = {
  id: string;
  name: string;
  owner_user_id: string;
  created_at: string;
  // Legacy family vault metadata (dead columns kept for back-compat). The
  // actual family key ciphertext lives in family_providers.api_key_ciphertext.
  family_salt: string | null;
  family_enc_blob_seed: string | null;
  // Owner-only toggle: when true, family turns resolve the BYOK key from the
  // owner's personal providers row (the owner's active personal key) instead
  // of family_providers. Surfaces only the boolean — no key material.
  use_owner_personal_key: boolean;
};

export type FamilyMemberRecord = {
  family_id: string;
  user_id: string;
  family_role: FamilyRole;
  family_display_name: string;
  relation: string;
  color: string;
  joined_at: string;
};

export type FamilyInviteRecord = {
  id: string;
  family_id: string;
  email: string;
  role: FamilyRole;
  expires_at: string;
  created_at: string;
  accepted_at: string | null;
  invited_by: string;
  // The token is NEVER in the wire shape — only the owner sees it (one-time,
  // out-of-band channel). The accept endpoint takes the token as a query/body
  // param on a separate route.
};

export type FamilyProviderRecord = {
  id: string;
  family_id: string;
  kind: ProviderKind;
  label: string;
  base_url: string | null;
  key_handle: string | null;
  model: string | null;
  // Embedding model for family semantic memory (null = off). Metadata only —
  // the recall embedding call reuses the family turn's sealed key. Optional:
  // pre-3c fixtures/rows may omit it.
  embeddings_model?: string | null;
  enc_blob: string | null;
};

// Owner-customisable system prompt for the ``fam`` persona. Mirrors
// ``FamilyTherapistPrompt`` from @ai-companion/contracts — kept as a local
// alias for consistency with the other family record types in this file.
export type FamilyTherapistPromptRecord = FamilyTherapistPrompt;

export type FamilyState = {
  family: FamilyRecord;
  members: FamilyMemberRecord[];
  invites: FamilyInviteRecord[];
  // Full list of family providers — multi-key surface since the BYOK
  // upgrade. ``provider`` below is the legacy singular pointer kept for
  // back-compat with the chat-side slice (the active family key the
  // family turn uses). New code should prefer ``providers`` and pick the
  // active row from the family Settings tab's selection.
  providers: FamilyProviderRecord[];
  provider: FamilyProviderRecord | null;
};

export type FamilyVaultMeta = {
  family_id: string;
  vault_initialized: boolean;
  family_salt: string | null;
  has_provider: boolean;
};

export function getFamily(): Promise<FamilyState> {
  // The server's ``GET /v1/family`` returns a raw (un-contracted) dict:
  //   { family, members, invites, providers: FamilyProvider[], vault: {...} }
  // The client's ``FamilyState`` exposes the full providers list (multi-key
  // since the BYOK upgrade) AND keeps the legacy singular ``provider`` for
  // back-compat (the chat-side ``familyProvider`` slice reads it; the
  // family Settings tab reads the list). Without carrying both, consumers
  // that haven't been migrated yet would lose the active-pointer state.
  return jsonFetch<{
    family: FamilyRecord;
    members: FamilyMemberRecord[];
    invites: FamilyInviteRecord[];
    providers: FamilyProviderRecord[];
  }>('/v1/family').then((raw) => ({
    family: raw.family,
    members: raw.members,
    invites: raw.invites,
    providers: raw.providers ?? [],
    // Legacy single-pointer field — ``loadFamily`` still uses it as the
    // default for the active family provider when the user has only one.
    provider: raw.providers?.[0] ?? null,
  }));
}

export function createFamily(body: { name: string }): Promise<FamilyRecord> {
  return jsonFetch<FamilyRecord>('/v1/family', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function renameFamily(body: { name: string }): Promise<FamilyRecord> {
  return jsonFetch<FamilyRecord>('/v1/family', {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function disbandFamily(): Promise<void> {
  return jsonFetch<void>('/v1/family', { method: 'DELETE' });
}

/** Owner-only toggle: when on, family turns resolve the BYOK key from the
 * owner's personal providers row (the owner's active personal key) instead of
 * family_providers. Returns the updated family record (the flag rides the
 * Family wire model back to all members). */
export function setFamilyUseOwnerPersonalKey(value: boolean): Promise<FamilyRecord> {
  return jsonFetch<FamilyRecord>('/v1/family/owner-personal-key', {
    method: 'PUT',
    body: JSON.stringify({ use_owner_personal_key: value }),
  });
}

export function listInvites(): Promise<FamilyInviteRecord[]> {
  return jsonFetch<FamilyInviteRecord[]>('/v1/family/invites');
}

export function sendInvite(body: { email: string }): Promise<FamilyInviteRecord> {
  return jsonFetch<FamilyInviteRecord>('/v1/family/invites', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function revokeInvite(iid: string): Promise<void> {
  return jsonFetch<void>(`/v1/family/invites/${iid}`, { method: 'DELETE' });
}

/** Accept a family invite. ``token`` is the one-time token from the email
 * (never the row id). 303-redirects to ``/family/welcome`` on success. */
export async function acceptFamilyInvite(token: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/v1/family/accept`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  return res.ok || res.status === 303;
}

export function leaveFamily(): Promise<void> {
  return jsonFetch<void>('/v1/family/members/me', { method: 'DELETE' });
}

export function removeFamilyMember(userId: string): Promise<void> {
  return jsonFetch<void>(`/v1/family/members/${userId}`, { method: 'DELETE' });
}

export function getFamilyVaultMeta(): Promise<FamilyVaultMeta> {
  return jsonFetch<FamilyVaultMeta>('/v1/family/vault/meta');
}

/** Clear the legacy family-vault metadata on the server: drops the
 * family_salt and family_enc_blob_seed on the families row. Used by the
 * family-key reset path so the owner can re-add a family key from scratch.
 * Owner-only server-side. */
export function clearFamilyVaultSeed(): Promise<{ family_id: string; ok: true }> {
  return jsonFetch<{ family_id: string; ok: true }>('/v1/family/vault', {
    method: 'PUT',
    body: JSON.stringify({ family_salt: null, family_enc_blob_seed: null }),
  });
}

export function listFamilyProviders(): Promise<FamilyProviderRecord[]> {
  return jsonFetch<FamilyProviderRecord[]>('/v1/family/providers');
}

export function createFamilyProvider(body: {
  kind: ProviderKind;
  label: string;
  base_url?: string | null;
  key_handle?: string | null;
  model?: string | null;
  embeddings_model?: string | null;
  // Legacy at-rest backup column — now unused. Send null.
  enc_blob?: string | null;
  // One-time ECDH-sealed plaintext key — same shape as createProvider's
  // enc_key_blob. The server envelope-encrypts it at rest.
  enc_key_blob?: string | null;
}): Promise<FamilyProviderRecord> {
  return jsonFetch<FamilyProviderRecord>('/v1/family/providers', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function deleteFamilyProvider(pid: string): Promise<void> {
  return jsonFetch<void>(`/v1/family/providers/${pid}`, { method: 'DELETE' });
}

/** Owner-customisable system prompt for the ``fam`` persona. Read by every
 * member (so they can see what their therapist is being told); written by
 * the owner only. ``body: null`` means "no customisation" — the client
 * renders its own copy of the static ``fam`` builtin (see
 * ``fixtures.tsx#FAM_BUILTIN_PROMPT``) instead of a re-shipped long blob. */
export function getFamilyTherapistPrompt(): Promise<FamilyTherapistPrompt> {
  return jsonFetch<FamilyTherapistPrompt>('/v1/family/therapist-prompt');
}

/** Owner-only. ``body: null`` clears the customisation (resets to the static
 * ``fam`` builtin). An empty string is a 400 — the contract is "set a real
 * prompt or pass null to clear". */
export function setFamilyTherapistPrompt(
  body: FamilyTherapistPromptSet,
): Promise<FamilyTherapistPrompt> {
  return jsonFetch<FamilyTherapistPrompt>('/v1/family/therapist-prompt', {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

// --- Billing (subscription purchase) ---
//
// Hosted-only (feature_billing and is_hosted). The purchase is a redirect to
// the provider's hosted checkout — Paddle (WW) or ЮKassa (RU) — so no card
// data is collected on our side (PCI-scope SAQ-A). ``credentials: 'include'``
// is already in the jsonFetch wrapper. The checkout callback redirect does
// NOT mutate state — webhooks are the single source of truth, so after the
// browser returns we just re-fetch the subscription (and the Principal via
// /v1/auth/me carries the refreshed plan + credits).

/** Public plan catalogue. ``geo=RU`` → ЮKassa/RUB plans, ``geo=WW`` → Paddle/USD
 * plans; omitted returns both. Empty on non-billing instances (the gate is
 * ``feature_billing and is_hosted``). The web renders its localized fixture
 * but validates the chosen slug against this list at checkout. */
export function listPlans(geo?: 'RU' | 'WW'): Promise<Plan[]> {
  const q = new URLSearchParams();
  if (geo) q.set('geo', geo);
  const qs = q.toString();
  return jsonFetch<Plan[]>(`/v1/billing/plans${qs ? `?${qs}` : ''}`);
}

/** Create a hosted-checkout session for ``plan_slug`` paid from
 * ``billing_country`` (RU → ЮKassa, else Paddle). On success the browser is
 * redirected to ``redirect_url`` (the provider's domain). 404 when billing is
 * off, 400 on a plan/geo mismatch, 503 when the provider isn't configured. */
export function createCheckout(body: CheckoutRequest): Promise<CheckoutSession> {
  return jsonFetch<CheckoutSession>('/v1/billing/checkout', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** The user's current subscription, or null on the free tier (no row). Drives
 * the billing tab's status + past_due banner. */
export function getSubscription(): Promise<Subscription | null> {
  return jsonFetch<Subscription | null>('/v1/billing/subscription');
}

/** Redirect to the provider's self-service portal (cancel, change card,
 * invoices). Managed by the provider — we never build our own cancel/card UI.
 * 503 when the provider isn't configured. */
export function createPortalSession(): Promise<PortalSession> {
  return jsonFetch<PortalSession>('/v1/billing/portal', { method: 'POST' });
}

// --- External messengers (Telegram first) ---
//
// The bot token is server-side envelope-encrypted (NOT zero-knowledge — the
// server can decrypt it; the UI says so). It never appears in these responses
// (only ``bot_token_masked`` — last 4 chars). BYOK keys bound at handshake ARE
// zero-knowledge up to the bind: the web unlocks the vault, ECDH-seals the
// active key to the server pubkey (same ``buildEncKeyBlob`` the chat uses), and
// the server envelope-wraps it ONCE. See ``app/connect/telegram/page.tsx``.

export type MessengerRecord = Messenger;
export type { Messenger, TelegramInitResponse } from '@ai-companion/contracts';

/** Status snapshot for one bot — drives the integrations card's auto-refresh. */
export type MessengerStatusInfo = {
  status: 'pending_handshake' | 'active' | 'paused' | 'error';
  persona_id: string;
  chat_id: number | null;
  last_error: string | null;
  last_seen_at: string | null;
  byok_bound: boolean;
};

export function listMessengers(): Promise<MessengerRecord[]> {
  return jsonFetch<MessengerRecord[]>('/v1/messengers');
}

/** Init: validate the bot token via Telegram getMe, create a
 * ``pending_handshake`` row, and return a connect token + deep-link URL the
 * user pastes into their bot's ``/start``. 400 on a bad token, 503 if Telegram
 * is unreachable or the messenger feature is disabled server-side. */
export function initTelegramBot(body: {
  bot_token: string;
  persona_id: string;
}): Promise<TelegramInitResponse> {
  return jsonFetch<TelegramInitResponse>('/v1/messengers/telegram', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** Complete the handshake. ``byok_enc_key_blob`` is the ECDH-sealed BYOK key
 * (same shape as ``/v1/llm/stream``'s ``enc_key_blob``), or null to use the
 * server-fallback chain. ``connectToken`` is the signed token from the init
 * response (sent as a query param — the server verifies it binds to this id). */
export function bindTelegramBot(
  messengerId: string,
  connectToken: string,
  body: { byok_enc_key_blob?: string | null },
): Promise<MessengerRecord> {
  const qs = new URLSearchParams({ token: connectToken });
  return jsonFetch<MessengerRecord>(
    `/v1/messengers/telegram/${messengerId}/bind?${qs.toString()}`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

export function patchMessenger(
  id: string,
  body: {
    persona_id?: string | null;
    status?: 'active' | 'paused' | 'error' | 'pending_handshake' | null;
  },
): Promise<MessengerRecord> {
  return jsonFetch<MessengerRecord>(`/v1/messengers/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function deleteMessenger(id: string): Promise<void> {
  return jsonFetch<void>(`/v1/messengers/${id}`, { method: 'DELETE' });
}

export function getMessengerStatus(id: string): Promise<MessengerStatusInfo> {
  return jsonFetch<MessengerStatusInfo>(`/v1/messengers/${id}/status`);
}
