"""Pydantic models — the Python side of the shared contracts.

Keep these in structural parity with the zod schemas in ``src/ts/index.ts``.
``scripts/check_drift.mjs`` compares the JSON-Schema emitted by
``Model.model_json_schema()`` against ``zod-to-json-schema``; any divergence
fails CI (``pnpm contracts:check``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ProviderKind(StrEnum):
    openai = "openai"
    anthropic = "anthropic"
    google = "google"
    openrouter = "openrouter"
    ollama = "ollama"
    # OpenAI-compatible fixed-origin aggregator (model id resolves server-side).
    aihubmix = "aihubmix"
    # Azure OpenAI — ``model`` carries the deployment name; ``base_url`` is the
    # resource endpoint; ``api_version`` travels via the per-request config.
    azure = "azure"
    # AWS Bedrock — key surface is an AWS access key + secret + region triple,
    # not a single API key. The picker renders a sub-form for these.
    bedrock = "bedrock"


class Tone(BaseModel):
    warmth: float = Field(..., ge=0, le=100)
    direct: float = Field(..., ge=0, le=100)
    pace: float = Field(..., ge=0, le=100)


class Provider(BaseModel):
    id: str
    user_id: str
    kind: ProviderKind
    label: str
    base_url: str | None = None
    key_handle: str | None = None  # server stores ONLY this, never the key
    # User-selected model id (e.g. "gpt-4o-mini", "ollama/llama3.3"). None =
    # use the server default for this kind. Not secret — travels in the request
    # body, not in the ECDH-sealed key blob.
    model: str | None = None
    # User-selected embedding model for semantic memory recall (e.g.
    # "text-embedding-3-small", "gemini/gemini-embedding-001"). None = semantic
    # memory off for this provider (recall uses the zero-config hash embedder /
    # server env embedder). The recall embedding call reuses the same
    # per-request ECDH-sealed BYOK key as the chat call. Not secret.
    embeddings_model: str | None = None
    # Zero-knowledge at-rest backup of the API key: XChaCha20-Poly1305 ciphertext
    # keyed by Argon2id(passphrase, salt), base64 of ``salt[16] || nonce[24] ||
    # ciphertext``. The salt is embedded so the blob is decryptable from the
    # passphrase alone. The server stores this but CANNOT decrypt it — the
    # passphrase is never sent. Used only to restore the vault after a browser
    # cache wipe; the per-request ECDH-sealed key flow is unchanged. None when the
    # client hasn't opted into sync (older rows / mock provider).
    enc_blob: str | None = None


class Persona(BaseModel):
    id: str
    user_id: str
    name: str
    role: str
    system_prompt: str
    tone: Tone
    opening_line: str
    custom: bool = False


class EventRole(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"


class Event(BaseModel):
    id: str
    user_id: str
    persona_id: str
    prev_event_id: str | None = None
    role: EventRole
    content: str
    # ``salience`` is the long-term recall weight (what matters for months).
    # ``short_term_salience`` boosts the item in the very next turns.
    # ``emotional_intensity`` captures the acute emotional charge of the moment.
    salience: float = Field(0.0, ge=0, le=1)
    short_term_salience: float = Field(0.0, ge=0, le=1)
    emotional_intensity: float = Field(0.0, ge=0, le=1)
    emotion_tags: list[str] = Field(default_factory=list)
    # Persisted-at timestamp (Phase 2a). None only for transient events that
    # never hit a store (e.g. the synthetic extractor event). Recall uses it
    # for time-based salience decay; events without it don't decay.
    created_at: datetime | None = None
    # Family scope (None for personal/non-family). visibility scopes recall:
    # "private" rows are recalled only by the participant_user_id member in
    # their own 1:1; "shared" rows are recalled by all family members in both
    # 1:1 and joint sessions. See PLAN §Family.
    family_id: str | None = None
    visibility: Literal["private", "shared"] = "private"
    participant_user_id: str | None = None


class EventChain(BaseModel):
    events: list[Event]
    salience_sum: float


class ConversationSummary(BaseModel):
    """A conversation-list projection aggregated from the event chain.

    K6: the UI drawer needs a first-class list of conversations (convo_id,
    title, last activity) that survives refresh. The store derives this from
    ``events`` (grouped by ``convo_id``) — there is no ``conversations`` table.
    ``title`` is the first user-role message truncated; ``preview`` is the
    last event content truncated; ``last_activity`` is the MAX(``created_at``)
    of the convo's events. Fields mirror ``Event`` family-scope columns so a
    conversation can be re-scoped into a family session later.
    """

    convo_id: str
    persona_id: str
    title: str
    preview: str
    event_count: int = Field(ge=0)
    created_at: datetime
    last_activity: datetime
    family_id: str | None = None
    visibility: Literal["private", "shared"] = "private"


class MemoryStatus(StrEnum):
    active = "active"
    superseded = "superseded"


class Memory(BaseModel):
    """An atomic, LLM-derived fact/observation the companion has extracted from
    the event chain — the display unit of the /memory page (replaces showing raw
    chat messages). One memory = one citable thing the companion knows about the
    user, with corpus-derived theme tags, a salience weight, and provenance
    (the event ids it was drawn from → "drawn from N turns").

    The event chain stays the recall substrate; memories are a synthesized view
    on top of it. Memories are mutable: extraction can update an existing memory
    (new tags, refined content) or supersede it (a newer memory replaces it).
    """

    id: str
    user_id: str
    persona_id: str
    content: str
    tags: list[str] = Field(default_factory=list)
    salience: float = Field(0.0, ge=0, le=1)
    source_event_ids: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.active
    created_at: datetime
    updated_at: datetime
    # Family scope (None for personal/non-family). Same visibility contract as
    # Event — see Event for the recall rules.
    family_id: str | None = None
    visibility: Literal["private", "shared"] = "private"
    participant_user_id: str | None = None


class MemoryShare(BaseModel):
    """A live link that lets one persona's memories be recalled by another
    persona — a *reference*, not a copy. The donor's memories stay owned by the
    donor (and mutated only by the donor's own turns); the receiver's read paths
    union the donor's active memories + event chains for as long as the link
    exists. Remove the row to revoke; nothing is duplicated or deleted.

    Donor-initiated: ``donor_persona_id`` shares INTO ``receiver_persona_id``.
    Both ids are opaque partition keys (builtins or ``custom-...``).
    """

    id: str
    user_id: str
    donor_persona_id: str
    receiver_persona_id: str
    created_at: datetime


class JournalEntry(BaseModel):
    """A user-authored diary entry — the display unit of the /journal page.

    Separate from the chat event chain: entries are written directly by the
    user (or seeded from a chat message via "Save to journal", which copies the
    message text into a new row and links the source convo/event). The journal
    surfaces ``mood`` and ``tags`` AS AUTHORED — by the user, not generated —
    so it never makes affective claims the user didn't make themselves
    ("disclose, don't perform"). ``salience`` is the user's "matters to me"
    choice, not an LLM-judged score.

    ``persona_id`` is the companion this entry relates to (Lou by default for
    journaling) — opaque partition key, same as ``Event.persona_id``.
    """

    id: str
    user_id: str
    persona_id: str
    title: str | None = None
    body: str
    mood: str | None = None
    tags: list[str] = Field(default_factory=list)
    salience: float = Field(0.0, ge=0, le=1)
    source_convo_id: str | None = None
    source_event_id: str | None = None
    created_at: datetime
    updated_at: datetime
    # Family scope (None for personal). Same visibility contract as Event/Memory.
    family_id: str | None = None
    visibility: Literal["private", "shared"] = "private"
    participant_user_id: str | None = None


class JournalTagListResponse(BaseModel):
    """Distinct tags the user has authored across their journal entries, scoped
    to the same filters as ``GET /v1/journal`` (minus ``tag``/``q``/pagination —
    the cloud is an aggregate, not a list of entries). Sorted lexicographically
    for a stable UI. Wrapped in an object so we can add fields (``counts``,
    ``moods``) without a contract break.

    Used by the /journal sidebar to keep the tag cloud stable across tag-filter
    changes — the cloud aggregates from the whole scope, not the filtered list
    of entries currently on screen.
    """

    tags: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    id: str
    user_id: str
    # Family-scoped cost: when the turn is a family session, the spend rolls up
    # against the family budget (per `family_id`), not the individual member.
    # None for personal sessions; the rollup falls back to `user_id` then.
    family_id: str | None = None
    # ``ProviderKind`` or ``"mock"`` — the mock adapter is not a provider but
    # still emits a usage row (cost 0). Typed ``str`` so the mock case is honest.
    provider_kind: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class RoutingNode(BaseModel):
    # ``ProviderKind`` or ``"mock"`` — the chain always ends in the mock
    # stand-in, which is a real node. Typed ``str`` so the mock case is honest.
    kind: str
    model: str
    base_url: str | None = None
    status: Literal["healthy", "standby", "unavailable"] = "standby"


class ProviderSummary(BaseModel):
    # ``ProviderKind`` or ``"mock"`` — the mock adapter still emits a usage row
    # (cost 0) and appears in the per-provider table. Typed ``str`` to be honest.
    kind: str
    model: str
    requests: int
    cost_usd: float
    tokens_in: int
    tokens_out: int
    status: Literal["healthy", "standby", "unavailable"] = "standby"


class RoutingState(BaseModel):
    chain: list[RoutingNode]
    monthly_budget_usd: float
    spent_usd: float
    remaining_usd: float
    fallback_last_turn: str | None = None
    pct: float = 0.0  # spent / monthly_budget, 0..1
    warn: bool = False  # soft-warn at >=80%
    hard_stop: bool = False  # hard-stop at >=100%
    per_provider: list[ProviderSummary] = Field(default_factory=list)
    langfuse_url: str | None = None


class LlmStreamRequest(BaseModel):
    persona_id: str
    convo_id: str
    message: str
    enc_key_blob: str | None = None
    key_handle: str | None = None
    # Optional user-selected model id (from the active Provider.model). When
    # set, the BYOK candidate uses it instead of the server default for the kind.
    model: str | None = None
    # Optional embedding model (from the active Provider.embeddings_model).
    # When set alongside a BYOK ``enc_key_blob``, memory recall embeds the
    # query + candidates semantically with the user's own key; on any failure
    # recall silently falls back to the hash embedder (never breaks a turn).
    embeddings_model: str | None = None
    memory_on: bool = True
    # Custom-persona override: when present, the backend builds the persona block
    # from this composed prompt (+ tone directives from persona_tone) instead of
    # the builtin registry / generic fallback. Sent only for custom personas
    # (the client lets builtins use the server-side registry). Not secret —
    # travels in the request body, not the ECDH-sealed key blob.
    persona_prompt: str | None = None
    persona_tone: Tone | None = None
    # --- Family scope (None for personal/non-family sessions) ---
    # The server validates: if ``family_id`` is set, principal.family_id must
    # equal it; if ``visibility == "shared"``, family_id must be set;
    # ``participant_user_id`` defaults to principal.user_id when absent;
    # ``enc_key_blob`` and ``family_enc_key_blob`` are mutually exclusive (400
    # if both — family turns use the family key + family budget, personal turns
    # use the personal key + personal budget).
    family_id: str | None = None
    visibility: Literal["private", "shared"] | None = None
    participant_user_id: str | None = None
    # Family BYOK blob — the family owner's API key, ECDH-sealed in the member's
    # browser from the family vault (separate from the personal vault). Same
    # zero-knowledge path as the personal enc_key_blob.
    family_enc_key_blob: str | None = None
    family_key_handle: str | None = None
    # BYOK base_url override (Ollama Cloud, custom OpenAI-compatible endpoints).
    # Optional — server falls back to the provider's default base_url.
    provider_base_url: str | None = None
    # I8: optional client-generated idempotency key. When set, the server dedups
    # persistence by (user_id, convo_id, request_id) so a retried turn (e.g.
    # after a connection drop) doesn't duplicate the user+assistant events or
    # fork the event chain. The stream still re-runs (the retrying client lost
    # the first response and needs a fresh one); only the side effects are
    # deduped. Omit for the legacy non-idempotent path.
    request_id: str | None = None


# --- SSE event discriminated union ---

class SessionEvent(BaseModel):
    type: Literal["session"]
    convo_id: str
    persona_id: str


class TokenEvent(BaseModel):
    type: Literal["token"]
    text: str


class FallbackEvent(BaseModel):
    type: Literal["fallback"]
    # ``ProviderKind`` or ``"mock"`` — a fallback can land on the mock stand-in.
    from_kind: str
    to_kind: str
    reason: str


class UsageEvent(BaseModel):
    type: Literal["usage"]
    # ``ProviderKind`` or ``"mock"`` — see ``Usage.provider_kind``.
    provider_kind: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class DoneEvent(BaseModel):
    type: Literal["done"]


class ErrorEvent(BaseModel):
    type: Literal["error"]
    # Redacted — never contains key material or provider error bodies.
    message: str


# --- Auth & deployment modes ---
#
# Identity is now a verified ``Principal`` resolved from a session cookie (see
# ``apps/api/.../auth/``), replacing the single-user ``X-User-Id`` self-assertion.
# The deployment mode + auth backend are selected at boot; the mode→backend matrix
# is enforced by ``auth/bootstrap.py``. These shapes are shared with the web so the
# UI can render mode-appropriate login/onboarding/nav from ``GET /v1/config`` and
# the current user from ``GET /v1/auth/me``. None of these carry key material —
# auth identity is fully decoupled from the BYOK vault (passphrase never leaves the
# browser and is never sent to any auth endpoint).

class DeploymentMode(StrEnum):
    self_hosted = "self_hosted"
    hosted = "hosted"


class SelfHostedProfile(StrEnum):
    # ``local`` — zero external dependencies: local accounts only.
    # ``sso``   — owner-configured OIDC / trusted-header / SAML.
    local = "local"
    sso = "sso"


class AuthBackendKind(StrEnum):
    local = "local"
    oidc = "oidc"
    magic_link = "magic_link"
    trusted_header = "trusted_header"


class Principal(BaseModel):
    """The verified identity attached to an authenticated request — the source of
    the ``user_id`` partition key that scopes every store query. Distinct from the
    vault passphrase (which the server never sees). Surfaced by ``GET /v1/auth/me``.
    """

    user_id: str
    # IdP subject (OIDC ``sub``, local user id, or the trusted-header value).
    subject: str
    # OIDC issuer URL, ``"local"``, or ``"trusted-header"``.
    issuer: str | None = None
    email: str | None = None
    display_name: str | None = None
    # Entitlements. ``self_hosted_free`` for self-hosted; hosted tiers carry credits.
    plan: str = "self_hosted_free"
    credits_usd: float = 0.0
    # Which AuthBackendKind authenticated this request.
    auth_backend: str
    # Family scope — None when the user is not in a family. family_role is only
    # meaningful when family_id is set. Each user is in at most one family.
    family_id: str | None = None
    family_role: Literal["owner", "member"] | None = None
    # Whether the user has confirmed ownership of their email. True for OIDC
    # (verified by the IdP) and magic-link (verified by link possession); for
    # local accounts it is True by default and False only when email
    # verification is enabled (FEATURE_EMAIL_VERIFICATION) and not yet completed.
    email_verified: bool = True


class SessionInfo(BaseModel):
    """One of the user's active sessions, surfaced by ``GET /v1/auth/sessions``
    for the "active devices" management card. The session ``token`` (the cookie
    value, a secret) is NEVER included — only the opaque surrogate ``id`` keys
    the revoke endpoints. ``current`` marks the session whose token matches the
    request cookie (cannot be revoked from its own card)."""

    id: str
    created_at: datetime
    expires_at: datetime
    user_agent: str | None = None
    current: bool = False


# --- Family types (multi-member, real per-user accounts) ---


class Family(BaseModel):
    """A family of up to 4 real user accounts that share a family therapist
    persona + family-scoped memory + a family BYOK key (owner's key, served to
    all members via the family vault). The owner is a regular user account; the
    family vault is a SEPARATE vault from the owner's personal vault, unlocked
    by a SEPARATE family passphrase that the owner shares with members
    out-of-band. The server cannot decrypt the family key — same zero-knowledge
    contract as the personal vault.
    """

    id: str
    name: str
    # The user_id of the family owner. The owner's personal account.
    owner_user_id: str
    created_at: datetime
    # Family vault metadata. The server cannot decrypt any of this — it's
    # XChaCha20-Poly1305 ciphertext keyed by Argon2id(family_passphrase, salt).
    # family_salt is needed by each member's browser to derive the master key.
    family_salt: str | None = None
    family_enc_blob_seed: str | None = None
    # Owner-only flag: when true, family chat turns resolve the BYOK key from
    # the owner's personal providers row instead of family_providers. The
    # server resolves the owner from owner_user_id (never a client value), so a
    # member cannot retarget the lookup. Surfaces only the boolean — no key
    # material — so returning it to all members is safe.
    use_owner_personal_key: bool = False


class FamilyRole(StrEnum):
    owner = "owner"
    member = "member"


class FamilyMember(BaseModel):
    """A user's membership in a family. ``family_display_name`` + ``relation``
    are what the therapist sees in the prompt (e.g. "Alex (parent)"). Defaults
    from ``users.display_name``; editable by the owner."""

    family_id: str
    user_id: str
    family_role: FamilyRole
    family_display_name: str
    # Free-form label: parent / partner / child / sibling / other. Not enum'd
    # server-side; the family owner picks from a UI palette.
    relation: str
    color: str
    joined_at: datetime


class FamilyInvite(BaseModel):
    """A pending invitation to join a family. The token is NEVER in the wire
    shape — it lives only in the email link (sealed; see magic-link pattern)."""

    id: str
    family_id: str
    email: str
    role: FamilyRole
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None = None
    invited_by: str


class FamilyProvider(BaseModel):
    """Family-scoped provider (the family owner's API key, shared with all
    members). Same shape as ``Provider`` but partitioned by ``family_id``. The
    ``enc_blob`` is encrypted to the family passphrase (separate from the
    personal passphrase). Server cannot decrypt it."""

    id: str
    family_id: str
    kind: ProviderKind
    label: str
    base_url: str | None = None
    key_handle: str | None = None  # server stores ONLY this, never the key
    model: str | None = None
    # Embedding model for family semantic memory recall (None = off). Not a
    # key — the recall embedding call reuses the family turn's sealed key.
    embeddings_model: str | None = None
    enc_blob: str | None = None
    created_at: datetime


class FamilyTherapistPrompt(BaseModel):
    """Owner-customisable system prompt for the ``fam`` persona, persisted on
    the family row. Read by every member (so they can see what their therapist
    is being told); written by the owner only.

    ``body`` is the composed prompt the owner has authored (session focus +
    family rules + family context + approach, with the unconditional
    "disclose, don't perform" footer appended client-side). ``None`` means
    the family has not customised the prompt — readers fall back to the
    static ``fam`` builtin in their own registry, so the server never has to
    re-ship the long builtin over the wire.

    ``set_by_display_name`` is the owner-side display name resolved at read
    time via the auth store so the client can render "Set by <name> · <date>"
    without a second round-trip. It's a denormalised cache, not authoritative;
    the real author is ``set_by_user_id``.
    """

    body: str | None = None
    set_by_user_id: str | None = None
    set_at: datetime | None = None
    set_by_display_name: str | None = None


class FamilyTherapistPromptSet(BaseModel):
    """Write body for ``PUT /v1/family/therapist-prompt``. ``None`` clears the
    customisation (resets to the static ``fam`` builtin). An empty string is a
    400 — the contract is "set a real prompt or pass null to clear"."""

    body: str | None = Field(default=None, max_length=8_000)


class FeatureFlags(BaseModel):
    # Server-derived, env-driven (not per-user). The web renders mode-appropriate
    # UI from these; per-user entitlements ride on ``Principal``.
    billing: bool = False
    credits: bool = False
    # Hosted server-fallback providers (LITELLM_API_KEY_*). Self-hosted defaults to
    # BYOK-or-mock.
    hosted_fallback: bool = False
    magic_links: bool = False
    # Email verification on local-account signup (requires SMTP). Off by default.
    email_verification: bool = False
    journal: bool = True
    shares: bool = True


class AuthConfig(BaseModel):
    """The public deployment descriptor returned by ``GET /v1/config``. Drives the
    login screen, onboarding steps, and nav. No secrets, no per-user data."""

    mode: DeploymentMode = DeploymentMode.self_hosted
    # Only meaningful when mode == self_hosted; null in hosted.
    profile: SelfHostedProfile | None = None
    # Backends the web may offer on the login screen (the enabled subset for this
    # deployment, already validated against the mode→backend matrix at boot).
    auth_backends: list[AuthBackendKind] = Field(default_factory=list)
    features: FeatureFlags = Field(default_factory=FeatureFlags)


class LocalSignupRequest(BaseModel):
    email: str
    # Local-account password — distinct from the vault passphrase. Hashed with
    # Argon2id server-side; never logged, never returned.
    password: str
    display_name: str | None = None
    # Interface language ("en" | "ru") at signup time, so the verification
    # email is sent in the language the user chose. None/unknown → English.
    lang: str | None = None


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class MagicLinkRequest(BaseModel):
    email: str


class ResendVerificationRequest(BaseModel):
    """Body for ``POST /v1/auth/verify-email/resend`` — re-sends the email
    verification link. The endpoint always acks ``{"ok": true}`` (non-enumerating:
    it never reveals whether the email has an account or is already verified)."""

    email: str
    # Interface language ("en" | "ru") for the re-sent email. None/unknown →
    # English. Lets a user who switched the UI language after signup get the
    # reminder in the now-current language.
    lang: str | None = None


# --- Billing (subscription purchase) ---
#
# Hosted-only capability (``feature_billing and is_hosted``). The web renders
# the plan grid + checkout from these; the actual purchase is a redirect to the
# provider's hosted checkout — Paddle (Merchant of Record) for international,
# ЮKassa for Russia. No card data is collected on our side (PCI-scope SAQ-A).
#
# Webhooks are the SINGLE source of truth for subscription state — the
# checkout callback redirect from the browser does NOT mutate state. A
# successful payment sets ``Principal.plan`` and refills ``credits_usd``
# (atomic); the existing ``out_of_credits`` gate and per-turn
# ``decrement_credits`` are unchanged. Provider routing is by
# ``billing_country`` (manual, account-derived), NOT by IP.

class BillingProvider(StrEnum):
    paddle = "paddle"
    yookassa = "yookassa"
    prodamus = "prodamus"  # RU acquirer; accepts RU cards + SBP AND foreign cards (WW)


class PlanGeo(StrEnum):
    WW = "WW"  # worldwide (Paddle USD/EUR, or Prodamus foreign cards)
    RU = "RU"  # Russia (ЮKassa RUB, or Prodamus RU cards/SBP)


class PlanInterval(StrEnum):
    month = "month"
    year = "year"


class SubscriptionStatus(StrEnum):
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    unpaid = "unpaid"


class Plan(BaseModel):
    """A sellable subscription tier. Prices in minor units (cents/kopecks) —
    never float. ``credits_grant_usd`` is the USD-denominated balance refilled
    on each successful payment (matches the existing ``credits_usd`` field);
    the payment currency (``currency``) may differ (RUB for RU plans). The
    grant is additive on renewal so an early renewal doesn't burn the user's
    remaining balance."""

    slug: str
    name: str
    price_cents: int = Field(..., ge=0)
    currency: str
    interval: PlanInterval
    geo: PlanGeo
    trial_days: int = Field(0, ge=0)
    credits_grant_usd: float = Field(0.0, ge=0)
    active: bool = True
    # The provider's price/plan id (Paddle price_id) — NULL until the operator
    # links the plan to a provider price in the dashboard. Checkout 503s while
    # this is unset (the plan isn't buyable yet). ЮKassa doesn't need it (the
    # amount is sent inline).
    provider_price_id: str | None = None


class Subscription(BaseModel):
    """The user's current subscription. ``provider_sub_id`` is the provider's
    subscription id (Paddle subscription id / ЮKassa recurring payment id).
    ``status`` is updated ONLY by webhook events — never by the checkout
    callback redirect. ``billing_country`` is fixed at checkout and selects
    which provider owns the lifecycle."""

    id: str
    user_id: str
    plan_slug: str
    provider: BillingProvider
    provider_sub_id: str | None = None
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool = False
    trial_ends_at: datetime | None = None
    billing_country: str | None = None
    created_at: datetime


class CheckoutRequest(BaseModel):
    """Body for ``POST /v1/billing/checkout``. ``billing_country`` selects the
    provider: ``RU`` → ЮKassa, otherwise Paddle. Manual override of the
    account-derived country — not IP-derived (IP is unreliable behind VPN)."""

    plan_slug: str
    billing_country: str


class CheckoutSession(BaseModel):
    """The hosted-checkout redirect. The browser leaves the app to the
    provider's domain; no card data is collected on our side. The plan+country
    are echoed server-side as passthrough metadata so the webhook can link the
    payment back to the user without trusting the redirect."""

    redirect_url: str
    provider: BillingProvider
    provider_sub_id: str | None = None


class PortalSession(BaseModel):
    """Self-service portal redirect (cancel, change card, invoices) — managed
    by the provider, not us. We never build our own cancel/card UI."""

    redirect_url: str


class BillingWebhookAck(BaseModel):
    """Minimal ack returned to the provider. Providers expect a 200 with a body
    they tolerate; keep it bare."""

    ok: bool = True


# --- External messengers (Telegram first; the shape generalizes) ---
#
# Per-user bot model: each user registers THEIR OWN bot via @BotFather and
# pastes the bot token in web Settings → Integrations. The server long-polls
# the Bot API (no webhook needed) and turns incoming Telegram DMs into regular
# chat turns against the SAME persona + memory the web chat uses.
#
# The bot token is envelope-encrypted at rest (Fernet keyed by
# MESSENGER_TOKEN_DEK); only the masked suffix leaves the server. The BYOK
# ``byok_enc_blob`` follows the same zero-knowledge contract as
# ``Provider.enc_blob``: the client ECDH-seals the decrypted key to the
# server session pubkey, the server wraps the ciphertext with the envelope
# key, and a turn decrypts inside the zeroize window only.


class MessengerKind(StrEnum):
    telegram = "telegram"


class MessengerStatus(StrEnum):
    pending_handshake = "pending_handshake"  # init done, awaiting /start + bind
    active = "active"
    paused = "paused"
    error = "error"


class Messenger(BaseModel):
    """A linked messenger bot. ``bot_token`` never appears here — only the
    masked suffix for display. ``byok_bound`` tells the UI whether a BYOK key
    was sealed during the handshake (false → server fallback keys)."""

    id: str
    user_id: str
    kind: MessengerKind
    status: MessengerStatus
    persona_id: str
    # Telegram-side identifiers learned from updates (None until first update).
    chat_id: int | None = None
    bot_username: str | None = None
    # Last 4 chars of the bot token, for display ("…ab12"). Never the full token.
    bot_token_masked: str
    byok_bound: bool = False
    last_error: str | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TelegramInitRequest(BaseModel):
    """Body for ``POST /v1/messengers/telegram`` — creates a pending messenger."""

    bot_token: str = Field(..., min_length=10, max_length=128)
    persona_id: str = Field(..., min_length=1, max_length=64)


class TelegramInitResponse(BaseModel):
    """The connect-token drives the Telegram-side /start deep link. The web
    shows it (and a t.me link) so the user can paste it into their bot."""

    messenger: Messenger
    connect_token: str
    connect_url: str  # https://t.me/<bot>?start=<connect_token>
    expires_at: datetime


class TelegramBindRequest(BaseModel):
    """Body for ``POST /v1/messengers/telegram/{id}/bind``.

    ``byok_enc_key_blob`` is the SAME ECDH-sealed shape the web chat sends on
    ``/v1/llm/stream`` (XChaCha20-Poly1305 sealed to the server session pubkey,
    base64). None = the bot uses server-fallback keys (env / mock). The server
    decrypts once, envelope-wraps the plaintext key material, and stores ONLY
    the envelope ciphertext."""

    byok_enc_key_blob: str | None = None


class MessengerPatchRequest(BaseModel):
    """PATCH semantics: omitted = keep, null = keep, present value = set."""

    persona_id: str | None = None
    status: MessengerStatus | None = None  # only active <-> paused transitions