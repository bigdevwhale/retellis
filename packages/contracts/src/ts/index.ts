// Shared contracts (zod) — mirrors src/py/ai_companion_contracts/models.py.
// `scripts/check_drift.mjs` compares the JSON-Schema emitted here against the
// pydantic side; any divergence fails CI (`pnpm contracts:check`).

import { z } from 'zod';

export const ProviderKind = z.enum([
  'openai',
  'anthropic',
  'google',
  'openrouter',
  'ollama',
  // OpenAI-compatible fixed-origin aggregator (model id resolves server-side).
  'aihubmix',
  // Azure OpenAI — model carries the deployment name; base_url is the resource
  // endpoint; api_version travels via the per-request config.
  'azure',
  // AWS Bedrock — key surface is an AWS access key + secret + region triple,
  // not a single API key. The picker renders a sub-form for these.
  'bedrock',
]);
export type ProviderKind = z.infer<typeof ProviderKind>;

export const Tone = z.object({
  warmth: z.number().min(0).max(100),
  direct: z.number().min(0).max(100),
  pace: z.number().min(0).max(100),
});
export type Tone = z.infer<typeof Tone>;

export const Provider = z.object({
  id: z.string(),
  user_id: z.string(),
  kind: ProviderKind,
  label: z.string(),
  base_url: z.string().nullable().nullish(),
  key_handle: z.string().nullable().nullish(),
  // User-selected model id for this provider (e.g. "gpt-4o-mini",
  // "ollama/llama3.3"). null = use the server default for this kind. Not secret
  // — travels in the request body, not in the ECDH-sealed key blob.
  model: z.string().nullable().nullish(),
  // User-selected embedding model for semantic memory recall (e.g.
  // "text-embedding-3-small", "gemini/gemini-embedding-001"). null = semantic
  // memory off for this provider (hash embedder / server env embedder). The
  // recall embedding call reuses the same per-request ECDH-sealed BYOK key as
  // the chat call. Not secret.
  embeddings_model: z.string().nullable().nullish(),
  // Zero-knowledge at-rest backup of the API key: XChaCha20-Poly1305 ciphertext
  // keyed by Argon2id(passphrase, salt), base64 of `salt[16] || nonce[24] ||
  // ciphertext`. The salt is embedded so the blob is decryptable from the
  // passphrase alone. The server stores this but CANNOT decrypt it — the
  // passphrase is never sent. Used only to restore the vault after a browser
  // cache wipe; the per-request ECDH-sealed key flow is unchanged. null when the
  // client hasn't opted into sync (older rows / mock provider).
  enc_blob: z.string().nullable().nullish(),
});
export type Provider = z.infer<typeof Provider>;

export const Persona = z.object({
  id: z.string(),
  user_id: z.string(),
  name: z.string(),
  role: z.string(),
  system_prompt: z.string(),
  tone: Tone,
  opening_line: z.string(),
  custom: z.boolean().default(false),
});
export type Persona = z.infer<typeof Persona>;

export const EventRole = z.enum(['user', 'assistant', 'system']);
export type EventRole = z.infer<typeof EventRole>;

export const Event = z.object({
  id: z.string(),
  user_id: z.string(),
  persona_id: z.string(),
  prev_event_id: z.string().nullable().nullish(),
  role: EventRole,
  content: z.string(),
  // salience = long-term recall weight; short_term_salience boosts next turns;
  // emotional_intensity captures acute emotional charge.
  salience: z.number().min(0).max(1).default(0),
  short_term_salience: z.number().min(0).max(1).default(0),
  emotional_intensity: z.number().min(0).max(1).default(0),
  emotion_tags: z.array(z.string()).default([]),
  // Persisted-at timestamp (ISO 8601; Phase 2a). null only for transient
  // events. Recall uses it for time-based salience decay.
  created_at: z.string().nullable().nullish(),
  // Family scope (null for personal/non-family sessions). visibility controls
  // recall scoping: "private" rows are recalled only by the participant_user_id
  // member in their own 1:1; "shared" rows are recalled by all family members
  // in both 1:1 and joint sessions. See PLAN.md §Family.
  family_id: z.string().nullable().nullish(),
  visibility: z.enum(['private', 'shared']).default('private'),
  participant_user_id: z.string().nullable().nullish(),
});
export type Event = z.infer<typeof Event>;

export const EventChain = z.object({
  events: z.array(Event),
  salience_sum: z.number(),
});
export type EventChain = z.infer<typeof EventChain>;

// K6: a conversation-list projection aggregated from the event chain. The UI
// drawer needs a first-class list that survives refresh; the store derives it
// from `events` grouped by convo_id (there is no `conversations` table). title
// = first user message truncated; preview = last event content truncated;
// last_activity = MAX(created_at). Mirrors Event family-scope columns so a
// conversation can be re-scoped into a family session later.
export const ConversationSummary = z.object({
  convo_id: z.string(),
  persona_id: z.string(),
  title: z.string(),
  preview: z.string(),
  event_count: z.number().int().min(0),
  created_at: z.string(),
  last_activity: z.string(),
  family_id: z.string().nullable().nullish(),
  visibility: z.enum(['private', 'shared']).default('private'),
});
export type ConversationSummary = z.infer<typeof ConversationSummary>;

// An atomic, LLM-derived fact the companion extracted from the event chain —
// the display unit of the /memory page (replaces showing raw chat messages).
// The event chain stays the recall substrate; memories are a synthesized view.
export const MemoryStatus = z.enum(['active', 'superseded']);
export type MemoryStatus = z.infer<typeof MemoryStatus>;

export const Memory = z.object({
  id: z.string(),
  user_id: z.string(),
  persona_id: z.string(),
  content: z.string(),
  tags: z.array(z.string()).default([]),
  salience: z.number().min(0).max(1).default(0),
  source_event_ids: z.array(z.string()).default([]),
  status: MemoryStatus.default('active'),
  created_at: z.string(),
  updated_at: z.string(),
  // Family scope (null for personal/non-family). See Event for the visibility
  // contract — the same rules apply to memory rows.
  family_id: z.string().nullable().nullish(),
  visibility: z.enum(['private', 'shared']).default('private'),
  participant_user_id: z.string().nullable().nullish(),
});
export type Memory = z.infer<typeof Memory>;

// A live link letting one persona's memories be recalled by another persona —
// a reference, not a copy. Donor-initiated: donor_persona_id shares INTO
// receiver_persona_id. Both ids are opaque partition keys.
export const MemoryShare = z.object({
  id: z.string(),
  user_id: z.string(),
  donor_persona_id: z.string(),
  receiver_persona_id: z.string(),
  created_at: z.string(),
});
export type MemoryShare = z.infer<typeof MemoryShare>;

// A user-authored diary entry — the display unit of the /journal page. Separate
// from the chat event chain: written by the user (or seeded from a chat message
// via "Save to journal", which copies the text and links the source). The
// journal surfaces mood/tags AS AUTHORED — by the user, not generated — so it
// never makes affective claims the user didn't make themselves ("disclose, don't
// perform"). salience is the user's "matters to me" choice, not an LLM score.
export const JournalEntry = z.object({
  id: z.string(),
  user_id: z.string(),
  persona_id: z.string(),
  title: z.string().nullable().nullish(),
  body: z.string(),
  mood: z.string().nullable().nullish(),
  tags: z.array(z.string()).default([]),
  salience: z.number().min(0).max(1).default(0),
  source_convo_id: z.string().nullable().nullish(),
  source_event_id: z.string().nullable().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
  // Family scope (null for personal). Same visibility contract as Event/Memory.
  family_id: z.string().nullable().nullish(),
  visibility: z.enum(['private', 'shared']).default('private'),
  participant_user_id: z.string().nullable().nullish(),
});
export type JournalEntry = z.infer<typeof JournalEntry>;

// Distinct tag cloud the /journal sidebar renders as filter chips. The server
// aggregates from the user's whole scope (matching persona/family/mood/date
// filters but NOT the active tag filter — otherwise the cloud collapses to
// ["<selected>"] whenever a chip is on). Wrapped in an object so the contract
// can grow fields (counts, moods) without a break.
export const JournalTagListResponse = z.object({
  tags: z.array(z.string()).default([]),
});
export type JournalTagListResponse = z.infer<typeof JournalTagListResponse>;

export const Usage = z.object({
  id: z.string(),
  user_id: z.string(),
  // Family-scoped cost: when the turn is a family session, the spend is rolled
  // up against the family budget (per `family_id`), not the individual member.
  // Null for personal sessions; the rollup falls back to `user_id` in that case.
  family_id: z.string().nullable().nullish(),
  // ProviderKind or 'mock' — the mock adapter is not a provider but still
  // emits a usage row (cost 0). Typed string so the mock case is honest.
  provider_kind: z.string(),
  model: z.string(),
  prompt_tokens: z.number().int(),
  completion_tokens: z.number().int(),
  cost_usd: z.number(),
});
export type Usage = z.infer<typeof Usage>;

export const RoutingNode = z.object({
  // ProviderKind or 'mock' — the chain always ends in the mock stand-in, which
  // is a real node. Typed string so the mock case is honest.
  kind: z.string(),
  model: z.string(),
  base_url: z.string().nullable().nullish(),
  status: z.enum(['healthy', 'standby', 'unavailable']).default('standby'),
});
export type RoutingNode = z.infer<typeof RoutingNode>;

export const ProviderSummary = z.object({
  // ProviderKind or 'mock' — the mock adapter still emits a usage row (cost 0)
  // and appears in the per-provider table. Typed string so the mock case is honest.
  kind: z.string(),
  model: z.string(),
  requests: z.number().int(),
  cost_usd: z.number(),
  tokens_in: z.number().int(),
  tokens_out: z.number().int(),
  status: z.enum(['healthy', 'standby', 'unavailable']).default('standby'),
});
export type ProviderSummary = z.infer<typeof ProviderSummary>;

export const RoutingState = z.object({
  chain: z.array(RoutingNode),
  monthly_budget_usd: z.number(),
  spent_usd: z.number(),
  remaining_usd: z.number(),
  fallback_last_turn: z.string().nullable().nullish(),
  pct: z.number().default(0),
  warn: z.boolean().default(false),
  hard_stop: z.boolean().default(false),
  per_provider: z.array(ProviderSummary).default([]),
  langfuse_url: z.string().nullable().nullish(),
});
export type RoutingState = z.infer<typeof RoutingState>;

export const LlmStreamRequest = z.object({
  persona_id: z.string(),
  convo_id: z.string(),
  message: z.string(),
  enc_key_blob: z.string().nullable().nullish(),
  key_handle: z.string().nullable().nullish(),
  // Optional user-selected model id (from the active Provider.model). When set,
  // the BYOK candidate uses it instead of the server default for the kind.
  model: z.string().nullable().nullish(),
  // Optional embedding model (from the active Provider.embeddings_model). When
  // set alongside a BYOK enc_key_blob, memory recall embeds semantically with
  // the user's own key; any failure silently falls back to the hash embedder.
  embeddings_model: z.string().nullable().nullish(),
  memory_on: z.boolean().default(true),
  // Custom-persona override: composed prompt + tone the backend builds the
  // persona block from (instead of the builtin registry). Sent only for custom
  // personas. Not secret — travels in the body, not the ECDH-sealed key blob.
  persona_prompt: z.string().nullable().nullish(),
  persona_tone: Tone.nullish(),
  // --- Family scope (null for personal/non-family sessions) ---
  // The server validates: if `family_id` is set, principal.family_id must equal
  // it; if `visibility == "shared"`, family_id must be set; `participant_user_id`
  // defaults to principal.user_id when absent; enc_key_blob and family_enc_key_blob
  // are mutually exclusive (400 if both).
  family_id: z.string().nullable().nullish(),
  visibility: z.enum(['private', 'shared']).nullish(),
  participant_user_id: z.string().nullable().nullish(),
  // Family BYOK blob — the family owner's API key, ECDH-sealed in the member's
  // browser from the family vault (separate from the personal vault). Travels
  // the same zero-knowledge path as the personal enc_key_blob.
  family_enc_key_blob: z.string().nullable().nullish(),
  family_key_handle: z.string().nullable().nullish(),
  // BYOK base_url override (Ollama Cloud, custom OpenAI-compatible endpoints).
  // Optional — server falls back to the provider's default base_url.
  provider_base_url: z.string().nullable().nullish(),
  // I8: optional client-generated idempotency key. When set, the server dedups
  // persistence by (user_id, convo_id, request_id) so a retried turn doesn't
  // duplicate events or fork the event chain. The stream still re-runs; only
  // the side effects are deduped. Omit for the legacy non-idempotent path.
  request_id: z.string().nullable().nullish(),
});
export type LlmStreamRequest = z.infer<typeof LlmStreamRequest>;

// --- SSE event discriminated union ---

export const SessionEvent = z.object({
  type: z.literal('session'),
  convo_id: z.string(),
  persona_id: z.string(),
});
export const TokenEvent = z.object({ type: z.literal('token'), text: z.string() });
export const FallbackEvent = z.object({
  type: z.literal('fallback'),
  // ProviderKind or 'mock' — a fallback can land on the mock stand-in.
  from_kind: z.string(),
  to_kind: z.string(),
  reason: z.string(),
});
export const UsageEvent = z.object({
  type: z.literal('usage'),
  // ProviderKind or 'mock' — see Usage.provider_kind.
  provider_kind: z.string(),
  model: z.string(),
  prompt_tokens: z.number().int(),
  completion_tokens: z.number().int(),
  cost_usd: z.number(),
});
export const DoneEvent = z.object({ type: z.literal('done') });
export const ErrorEvent = z.object({ type: z.literal('error'), message: z.string() });

// --- Auth & deployment modes ---
//
// Identity is now a verified Principal resolved from a session cookie (see
// apps/api/.../auth/), replacing the single-user X-User-Id self-assertion. The
// deployment mode + auth backend are selected at boot; the mode→backend matrix is
// enforced by auth/bootstrap.py. These shapes are shared with the web so the UI
// renders mode-appropriate login/onboarding/nav from GET /v1/config and the
// current user from GET /v1/auth/me. None carry key material — auth identity is
// fully decoupled from the BYOK vault (passphrase never leaves the browser and is
// never sent to any auth endpoint).

export const DeploymentMode = z.enum(['self_hosted', 'hosted']);
export type DeploymentMode = z.infer<typeof DeploymentMode>;

export const SelfHostedProfile = z.enum(['local', 'sso']);
export type SelfHostedProfile = z.infer<typeof SelfHostedProfile>;

export const AuthBackendKind = z.enum(['local', 'oidc', 'magic_link', 'trusted_header']);
export type AuthBackendKind = z.infer<typeof AuthBackendKind>;

// The verified identity attached to an authenticated request — the source of the
// user_id partition key that scopes every store query. Distinct from the vault
// passphrase (which the server never sees). Surfaced by GET /v1/auth/me.
export const Principal = z.object({
  user_id: z.string(),
  // IdP subject (OIDC sub, local user id, or the trusted-header value).
  subject: z.string(),
  // OIDC issuer URL, "local", or "trusted-header".
  issuer: z.string().nullable().nullish(),
  email: z.string().nullable().nullish(),
  display_name: z.string().nullable().nullish(),
  // Entitlements. "self_hosted_free" for self-hosted; hosted tiers carry credits.
  plan: z.string().default('self_hosted_free'),
  credits_usd: z.number().default(0),
  // Which AuthBackendKind authenticated this request.
  auth_backend: z.string(),
  // Family scope — null when the user is not in a family. family_role is only
  // meaningful when family_id is set. Each user is in at most one family.
  family_id: z.string().nullable().nullish(),
  family_role: z.enum(['owner', 'member']).nullish(),
  // Whether the user has confirmed ownership of their email. True for OIDC
  // (verified by the IdP) and magic-link (verified by link possession); for
  // local accounts it is True by default and False only when email
  // verification is enabled (FEATURE_EMAIL_VERIFICATION) and not yet completed.
  email_verified: z.boolean().default(true),
});
export type Principal = z.infer<typeof Principal>;

// --- Family types (multi-member, real per-user accounts) ---

export const Family = z.object({
  id: z.string(),
  name: z.string(),
  // The user_id of the family owner. The owner's personal account; the family
  // BYOK key lives in the family vault and is decrypted via the family
  // passphrase that the owner shares with members out-of-band.
  owner_user_id: z.string(),
  created_at: z.string(),
  // Family vault metadata. The server cannot decrypt any of this — it's
  // XChaCha20-Poly1305 ciphertext keyed by Argon2id(family_passphrase, salt).
  // family_salt is needed by each member's browser to derive the master key.
  family_salt: z.string().nullable().nullish(),
  family_enc_blob_seed: z.string().nullable().nullish(),
  // Owner-only flag: when true, family chat turns resolve the BYOK key from
  // the owner's personal providers row instead of family_providers. The
  // server resolves the owner from owner_user_id (never a client value), so a
  // member cannot retarget the lookup. Surfaces only the boolean — no key
  // material — so returning it to all members is safe.
  use_owner_personal_key: z.boolean().default(false),
});
export type Family = z.infer<typeof Family>;

export const FamilyRole = z.enum(['owner', 'member']);
export type FamilyRole = z.infer<typeof FamilyRole>;

export const FamilyMember = z.object({
  family_id: z.string(),
  user_id: z.string(),
  family_role: FamilyRole,
  // Display name + relation shown to the therapist in the prompt (e.g.
  // "Alex (parent)"). Defaults from users.display_name; editable by the owner.
  family_display_name: z.string(),
  // Free-form label: parent / partner / child / sibling / other. Not enum'd
  // server-side; the family owner picks from a UI palette.
  relation: z.string(),
  color: z.string(),
  joined_at: z.string(),
});
export type FamilyMember = z.infer<typeof FamilyMember>;

export const FamilyInvite = z.object({
  id: z.string(),
  family_id: z.string(),
  email: z.string(),
  role: FamilyRole,
  expires_at: z.string(),
  created_at: z.string(),
  accepted_at: z.string().nullable().nullish(),
  // The token is NEVER in the wire shape — it lives only in the email link.
  invited_by: z.string(),
});
export type FamilyInvite = z.infer<typeof FamilyInvite>;

// Family-scoped provider (the family owner's API key, shared with members).
// Same shape as `Provider` but partitioned by `family_id`. The enc_blob is
// encrypted to the family passphrase (separate from the personal passphrase).
export const FamilyProvider = z.object({
  id: z.string(),
  family_id: z.string(),
  kind: ProviderKind,
  label: z.string(),
  base_url: z.string().nullable().nullish(),
  key_handle: z.string().nullable().nullish(),
  model: z.string().nullable().nullish(),
  // Embedding model for family semantic memory recall (null = off). Not a
  // key — the recall embedding call reuses the family turn's sealed key.
  embeddings_model: z.string().nullable().nullish(),
  enc_blob: z.string().nullable().nullish(),
  created_at: z.string(),
});
export type FamilyProvider = z.infer<typeof FamilyProvider>;

// Owner-customisable system prompt for the `fam` persona, persisted on the
// family row. Read by every member so they can see what their therapist is
// being told; written by the owner only. `body` is the composed prompt the
// owner has authored; null means "fall back to the static `fam` builtin"
// (which the client keeps in its own registry, so the server never re-ships
// the long builtin). `set_by_display_name` is denormalised — the real
// author is `set_by_user_id`. Mirrors the pydantic FamilyTherapistPrompt.
export const FamilyTherapistPrompt = z.object({
  body: z.string().nullable().nullish(),
  set_by_user_id: z.string().nullable().nullish(),
  set_at: z.string().nullable().nullish(),
  set_by_display_name: z.string().nullable().nullish(),
});
export type FamilyTherapistPrompt = z.infer<typeof FamilyTherapistPrompt>;

// Write body for PUT /v1/family/therapist-prompt. null clears the
// customisation (resets to the static `fam` builtin). Empty string is a 400
// — the contract is "set a real prompt or pass null to clear".
export const FamilyTherapistPromptSet = z.object({
  body: z.string().max(8_000).nullable(),
});
export type FamilyTherapistPromptSet = z.infer<typeof FamilyTherapistPromptSet>;

// Server-derived, env-driven (not per-user). The web renders mode-appropriate UI
// from these; per-user entitlements ride on Principal.
export const FeatureFlags = z.object({
  billing: z.boolean().default(false),
  credits: z.boolean().default(false),
  // Hosted server-fallback providers (LITELLM_API_KEY_*). Self-hosted defaults to
  // BYOK-or-mock.
  hosted_fallback: z.boolean().default(false),
  magic_links: z.boolean().default(false),
  // Email verification on local-account signup (requires SMTP). Off by default.
  email_verification: z.boolean().default(false),
  journal: z.boolean().default(true),
  shares: z.boolean().default(true),
});
export type FeatureFlags = z.infer<typeof FeatureFlags>;

// The public deployment descriptor returned by GET /v1/config. Drives the login
// screen, onboarding steps, and nav. No secrets, no per-user data.
export const AuthConfig = z.object({
  mode: DeploymentMode.default('self_hosted'),
  // Only meaningful when mode === self_hosted; null in hosted.
  profile: SelfHostedProfile.nullish(),
  // Backends the web may offer on the login screen (the enabled subset for this
  // deployment, already validated against the mode→backend matrix at boot).
  auth_backends: z.array(AuthBackendKind).default([]),
  features: FeatureFlags.default({}),
});
export type AuthConfig = z.infer<typeof AuthConfig>;

export const LocalSignupRequest = z.object({
  email: z.string(),
  // Local-account password — distinct from the vault passphrase. Hashed with
  // Argon2id server-side; never logged, never returned.
  password: z.string(),
  display_name: z.string().nullable().nullish(),
});
export type LocalSignupRequest = z.infer<typeof LocalSignupRequest>;

export const LocalLoginRequest = z.object({
  email: z.string(),
  password: z.string(),
});
export type LocalLoginRequest = z.infer<typeof LocalLoginRequest>;

export const MagicLinkRequest = z.object({ email: z.string() });
export type MagicLinkRequest = z.infer<typeof MagicLinkRequest>;

// Body for POST /v1/auth/verify-email/resend. The endpoint always acks
// {"ok": true} (non-enumerating: never reveals whether the email has an
// account or is already verified).
export const ResendVerificationRequest = z.object({ email: z.string() });
export type ResendVerificationRequest = z.infer<typeof ResendVerificationRequest>;

// --- Billing (subscription purchase) ---
//
// Hosted-only capability (feature_billing and is_hosted). The web renders the
// plan grid + checkout from these; the actual purchase is a redirect to the
// provider's hosted checkout — Paddle (Merchant of Record) for international,
// ЮKassa for Russia. No card data is collected on our side (PCI-scope SAQ-A).
//
// Webhooks are the SINGLE source of truth for subscription state — the checkout
// callback redirect from the browser does NOT mutate state. A successful
// payment sets Principal.plan and refills credits_usd (atomic); the existing
// out_of_credits gate and per-turn decrement_credits are unchanged. Provider
// routing is by billing_country (manual, account-derived), NOT by IP.

export const BillingProvider = z.enum(['paddle', 'yookassa', 'prodamus']);
export type BillingProvider = z.infer<typeof BillingProvider>;

export const PlanGeo = z.enum(['WW', 'RU']);
export type PlanGeo = z.infer<typeof PlanGeo>;

export const PlanInterval = z.enum(['month', 'year']);
export type PlanInterval = z.infer<typeof PlanInterval>;

export const SubscriptionStatus = z.enum(['trialing', 'active', 'past_due', 'canceled', 'unpaid']);
export type SubscriptionStatus = z.infer<typeof SubscriptionStatus>;

// A sellable subscription tier. Prices in minor units (cents/kopecks) — never
// float. credits_grant_usd is the USD-denominated balance refilled on each
// successful payment; the payment currency may differ (RUB for RU plans). The
// grant is additive on renewal so an early renewal doesn't burn the balance.
export const Plan = z.object({
  slug: z.string(),
  name: z.string(),
  price_cents: z.number().int().min(0),
  currency: z.string(),
  interval: PlanInterval,
  geo: PlanGeo,
  trial_days: z.number().int().min(0).default(0),
  credits_grant_usd: z.number().min(0).default(0),
  active: z.boolean().default(true),
  provider_price_id: z.string().nullable().nullish(),
});
export type Plan = z.infer<typeof Plan>;

// The user's current subscription. status is updated ONLY by webhook events —
// never by the checkout callback redirect. billing_country is fixed at
// checkout and selects which provider owns the lifecycle.
export const Subscription = z.object({
  id: z.string(),
  user_id: z.string(),
  plan_slug: z.string(),
  provider: BillingProvider,
  provider_sub_id: z.string().nullable().nullish(),
  status: SubscriptionStatus,
  current_period_start: z.string(),
  current_period_end: z.string(),
  cancel_at_period_end: z.boolean().default(false),
  trial_ends_at: z.string().nullable().nullish(),
  billing_country: z.string().nullable().nullish(),
  created_at: z.string(),
});
export type Subscription = z.infer<typeof Subscription>;

// Body for POST /v1/billing/checkout. billing_country selects the provider:
// RU → ЮKassa, otherwise Paddle. Manual override of the account-derived
// country — not IP-derived (IP is unreliable behind VPN).
export const CheckoutRequest = z.object({
  plan_slug: z.string(),
  billing_country: z.string(),
});
export type CheckoutRequest = z.infer<typeof CheckoutRequest>;

// The hosted-checkout redirect. The browser leaves the app to the provider's
// domain; no card data is collected on our side. The plan+country are echoed
// server-side as passthrough metadata so the webhook can link the payment back
// to the user without trusting the redirect.
export const CheckoutSession = z.object({
  redirect_url: z.string(),
  provider: BillingProvider,
  provider_sub_id: z.string().nullable().nullish(),
});
export type CheckoutSession = z.infer<typeof CheckoutSession>;

// Self-service portal redirect (cancel, change card, invoices) — managed by
// the provider, not us. We never build our own cancel/card UI.
export const PortalSession = z.object({ redirect_url: z.string() });
export type PortalSession = z.infer<typeof PortalSession>;

// Minimal ack returned to the provider. Providers expect a 200 with a body
// they tolerate; keep it bare.
export const BillingWebhookAck = z.object({ ok: z.boolean().default(true) });
export type BillingWebhookAck = z.infer<typeof BillingWebhookAck>;

// --- External messengers (Telegram first; the shape generalizes) ---

export const MessengerKind = z.enum(['telegram']);
export type MessengerKind = z.infer<typeof MessengerKind>;

export const MessengerStatus = z.enum(['pending_handshake', 'active', 'paused', 'error']);
export type MessengerStatus = z.infer<typeof MessengerStatus>;

// A linked messenger bot. ``bot_token`` never appears here — only the masked
// suffix for display. ``byok_bound`` tells the UI whether a BYOK key was
// sealed during the handshake (false → server fallback keys).
export const Messenger = z.object({
  id: z.string(),
  user_id: z.string(),
  kind: MessengerKind,
  status: MessengerStatus,
  persona_id: z.string(),
  chat_id: z.number().int().nullable().nullish(),
  bot_username: z.string().nullable().nullish(),
  bot_token_masked: z.string(),
  byok_bound: z.boolean().default(false),
  last_error: z.string().nullable().nullish(),
  last_seen_at: z.string().nullable().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Messenger = z.infer<typeof Messenger>;

// Body for ``POST /v1/messengers/telegram`` — creates a pending messenger.
export const TelegramInitRequest = z.object({
  bot_token: z.string().min(10).max(128),
  persona_id: z.string().min(1).max(64),
});
export type TelegramInitRequest = z.infer<typeof TelegramInitRequest>;

// The connect-token drives the Telegram-side /start deep link. The web shows
// it (and a t.me link) so the user can paste it into their bot.
export const TelegramInitResponse = z.object({
  messenger: Messenger,
  connect_token: z.string(),
  connect_url: z.string(),
  expires_at: z.string(),
});
export type TelegramInitResponse = z.infer<typeof TelegramInitResponse>;

// Body for ``POST /v1/messengers/telegram/{id}/bind``.
// ``byok_enc_key_blob`` is the SAME ECDH-sealed shape the web chat sends on
// ``/v1/llm/stream`` (XChaCha20-Poly1305 sealed to the server session pubkey,
// base64). None = the bot uses server-fallback keys (env / mock).
export const TelegramBindRequest = z.object({
  byok_enc_key_blob: z.string().nullable().nullish(),
});
export type TelegramBindRequest = z.infer<typeof TelegramBindRequest>;

// PATCH semantics: omitted = keep, null = keep, present value = set.
export const MessengerPatchRequest = z.object({
  persona_id: z.string().nullable().nullish(),
  status: MessengerStatus.nullable().nullish(),
});
export type MessengerPatchRequest = z.infer<typeof MessengerPatchRequest>;

// One of the user's active sessions, surfaced by GET /v1/auth/sessions for the
// "active devices" management card. The session token (the cookie value, a
// secret) is NEVER included — only the opaque surrogate id keys the revoke
// endpoints. current marks the session whose token matches the request cookie.
export const SessionInfo = z.object({
  id: z.string(),
  created_at: z.string(),
  expires_at: z.string(),
  user_agent: z.string().nullable().nullish(),
  current: z.boolean().default(false),
});
export type SessionInfo = z.infer<typeof SessionInfo>;

export const LlmStreamEvent = z.discriminatedUnion('type', [
  SessionEvent,
  TokenEvent,
  FallbackEvent,
  UsageEvent,
  DoneEvent,
  ErrorEvent,
]);
export type LlmStreamEvent = z.infer<typeof LlmStreamEvent>;

// Registry used by the drift check (names must match pydantic model names).
export const REGISTRY = {
  Tone,
  Provider,
  Persona,
  Event,
  EventChain,
  ConversationSummary,
  Memory,
  MemoryShare,
  JournalEntry,
  JournalTagListResponse,
  Usage,
  RoutingNode,
  ProviderSummary,
  RoutingState,
  LlmStreamRequest,
  SessionEvent,
  TokenEvent,
  FallbackEvent,
  UsageEvent,
  DoneEvent,
  ErrorEvent,
  Principal,
  Family,
  FamilyMember,
  FamilyInvite,
  FamilyProvider,
  FamilyTherapistPrompt,
  FamilyTherapistPromptSet,
  FeatureFlags,
  AuthConfig,
  LocalSignupRequest,
  LocalLoginRequest,
  MagicLinkRequest,
  ResendVerificationRequest,
  SessionInfo,
  Plan,
  Subscription,
  CheckoutRequest,
  CheckoutSession,
  PortalSession,
  BillingWebhookAck,
  Messenger,
  TelegramInitRequest,
  TelegramInitResponse,
  TelegramBindRequest,
  MessengerPatchRequest,
};
