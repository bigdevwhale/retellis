"""Application settings (pydantic-settings).

Real LLM keys live here ONLY for the server-fallback path. They are never
logged and never returned by any endpoint. BYOK keys (the primary path) arrive
per-request as an ECDH-encrypted blob and are zeroized after one LiteLLM call.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Server-side fallback keys (optional; empty → mock adapter) ---
    litellm_api_key_openai: str = ""
    litellm_api_key_anthropic: str = ""
    litellm_api_key_openrouter: str = ""
    litellm_api_key_google: str = ""
    # Azure OpenAI — key in ``litellm_api_key_azure``, the resource endpoint in
    # ``azure_api_base``, the api_version in ``azure_api_version`` (litellm
    # naming). The key alone is not enough to make a call.
    litellm_api_key_azure: str = ""
    azure_api_base: str = ""
    azure_api_version: str = ""
    # AIHubMix — OpenAI-compatible fixed-origin gateway. The base URL is hard-
    # coded in the chain; only the api_key is needed.
    litellm_api_key_aihubmix: str = ""
    # AWS Bedrock — three fields (access key, secret, region) live together;
    # setting just ``litellm_api_key_bedrock`` is treated as the access key.
    # Use the same key surface for symmetry with the other providers.
    litellm_api_key_bedrock: str = ""
    aws_secret_access_key: str = ""
    aws_region_name: str = ""
    anthropic_api_key: str = ""

    # --- Infra ---
    database_url: str = "postgresql+asyncpg://companion:companion@postgres:5432/companion"
    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_host: str = "http://langfuse:3000"
    # Browser-facing Langfuse URL (the compose exposes it on :3001). Used only
    # for the routing dashboard link-out; ``langfuse_host`` is the in-network
    # address the API uses for tracing.
    langfuse_public_url: str = "http://localhost:3001"

    # --- Routing / budget ---
    monthly_budget_usd: float = 20.0
    ollama_base_url: str = ""
    # P2: model id for post-turn UTILITY calls (the salience judge). Empty →
    # per-kind cheap sibling (``UTILITY_MODELS`` in llm/provider.py), falling
    # back to the serving model for kinds without one. Set to pin one model
    # for all kinds (e.g. a local ollama tag). Extraction/consolidation always
    # use the serving model — they write user-visible memory content.
    utility_model: str = ""

    # --- Embeddings (Phase 1a: semantic recall upgrade) ---
    # ``hash`` (zero-config default) uses the deterministic feature-hashing
    # embedder — no API call, works offline. ``semantic`` batches query-time
    # embeddings through litellm (OpenAI-compatible, 384-dim truncation) and
    # falls back to hash on any failure, so recall never breaks. The stored
    # ``events.embedding`` column keeps the write-path hash vectors either way
    # (it is unused by the re-embed recall path; switching it to semantic
    # vectors is part of the pgvector ANN upgrade, I11 post-MVP).
    embeddings_mode: str = "hash"
    embeddings_model: str = "text-embedding-3-small"
    # Dedicated key for the embeddings call; falls back to
    # ``litellm_api_key_openai`` when empty. Never logged.
    embeddings_api_key: str = ""

    # --- Memory store selection ---
    # When true, the app uses the Postgres event store (compose sets this).
    # Otherwise (or on connection failure) it uses the in-memory store.
    use_db: bool = Field(default=False, validation_alias=AliasChoices("USE_DB", "COMPANION_USE_DB"))

    # --- Server ---
    # Comma-separated allowed CORS origins. Include both `localhost` and
    # `127.0.0.1` forms of the web origin — they are distinct origins for CORS,
    # and opening the app at `127.0.0.1:3000` while only `localhost:3000` is
    # listed silently blocks every API call from the browser (curl still works,
    # which makes it hard to spot). Set CORS_ORIGINS in .env to add more.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://0.0.0.0:3000"
    default_user_id: str = "00000000-0000-0000-0000-000000000000"

    # --- Deployment mode + auth ---
    # ``self_hosted`` (default) is the zero-cloud, owner-configured deployment.
    # ``hosted`` is the managed SaaS (managed OAuth + magic-link + billing). The
    # mode→backend matrix is enforced at boot by ``auth/bootstrap.py``.
    deployment_mode: str = "self_hosted"
    # Only meaningful when mode == self_hosted. ``local`` (default) restricts to
    # local accounts only — no external IdP, no SMTP, no callback URL. ``sso``
    # allows oidc / trusted_header (and magic_link only with SMTP configured).
    auth_self_hosted_profile: str = "local"
    # Which auth backend serves login. Validated against the matrix at boot.
    auth_backend: str = "local"
    # Public origin the browser uses to reach the app (used to build OIDC
    # redirect URIs and magic-link URLs). In compose, Caddy fronts one origin.
    public_origin: str = "http://localhost:3000"
    # Session cookie name + lifetime. The cookie is HttpOnly + Secure +
    # SameSite=Lax and carries an opaque session token (row in ``sessions``).
    auth_session_cookie: str = "retellis_sess"
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    # Secret for signing magic-link tokens (HMAC-SHA256). Required when
    # auth_backend == magic_link. Never logged.
    auth_magic_link_secret: str = ""
    # Secret for signing the OIDC state cookie (HMAC-SHA256). Required when
    # auth_backend == oidc. Never logged.
    auth_state_secret: str = ""
    # Secret for signing family-invite tokens (HMAC-SHA256). Falls back to
    # auth_state_secret when empty so a self-hosted owner who only configured
    # one secret still works. Never logged.
    auth_invite_secret: str = ""
    # Email transport: ``console`` (default — prints the link, for local/dev),
    # ``smtp``, or ``off`` (magic-link disabled). Self-hosted local profile
    # doesn't need email.
    auth_email_transport: str = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # STARTTLS policy for the SMTP transport: ``required`` (default — always
    # upgrade, for external providers like Gmail/Mailgun on 587), ``if_supported``
    # (upgrade only when the server advertises STARTTLS), or ``never`` (plain
    # SMTP — for an internal postfix relay on port 25 with no TLS cert). Port 465
    # is implicit TLS (SMTPS) and ignores this setting.
    smtp_starttls: str = "required"
    # Email verification (local-account signup). When feature_email_verification
    # is on, new local signups start email_verified=false and a verification
    # link is emailed; bootstrap requires auth_email_transport == "smtp" and a
    # signing secret. Falls back to auth_magic_link_secret when this is empty so an
    # operator who already set one secret is covered. Never logged.
    auth_email_verification_secret: str = ""
    # Verification-link TTL (longer than magic-link's 15 min — verification is
    # less urgent and the user may click hours later).
    auth_email_verification_ttl_seconds: int = 24 * 60 * 60

    # --- OIDC backend config (auth_backend == oidc) ---
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""  # some IdPs allow PKCE-only public clients
    oidc_scopes: str = "openid email profile"
    # Comma-separated provider labels offered on the login screen in hosted mode
    # (e.g. "Google,GitHub"). Purely cosmetic; the actual provider is ``oidc_issuer``.
    oidc_display_name: str = "Single Sign-On"

    # --- Trusted-header backend (auth_backend == trusted_header) ---
    # The front proxy (OAuth2 Proxy / Authelia / Traefik Forward Auth / Caddy /
    # nginx auth_request) authenticates the user and sets a header FastAPI trusts
    # — ONLY behind an HMAC signature (``auth_header_hmac_secret``) so the header
    # can't be spoofed. Never expose the API directly when this backend is on.
    auth_trusted_header_name: str = "X-Remote-User"
    auth_trusted_header_sig_name: str = "X-Remote-User-Sig"
    auth_header_hmac_secret: str = ""
    # When true, additionally require the request to originate from a private /
    # loopback address (the proxy on the internal network). Default true.
    auth_trusted_require_internal: bool = True

    # --- Hosted entitlements ---
    # Starting credit grant for new hosted accounts (USD). 0 in self-hosted.
    hosted_signup_credits_usd: float = 0.0
    # Feature flags surfaced via GET /v1/config (env-driven, not per-user).
    feature_billing: bool = False
    feature_credits: bool = False
    feature_hosted_fallback: bool = False
    feature_magic_links: bool = False
    # Email verification on local-account signup (soft — session is still issued
    # immediately; the flag enables the unverified-start + verification email).
    # Bootstrap rejects enabling it unless AUTH_BACKEND=local + SMTP transport +
    # a signing secret. Default off → no behavior change for existing deployments.
    feature_email_verification: bool = False

    # --- Billing (hosted-only; gated `feature_billing and is_hosted`) ---
    # Two providers cover two geographies: Paddle (Merchant of Record) for
    # international (WW) and ЮKassa for Russia (RU). Routing is by the user's
    # billing_country (manual), NOT by IP. All secrets — never logged, never
    # returned; the redaction filter scrubs `paddle_`/`yukassa_` token prefixes.
    # ``validate_auth_config`` hard-fails the boot when hosted + feature_billing
    # but neither provider is configured.
    paddle_api_key: str = ""
    paddle_webhook_secret: str = ""  # HMAC-SHA256 verifier for Paddle webhook signatures
    paddle_environment: str = "sandbox"  # "sandbox" | "production"
    paddle_vendor_id: str = ""  # Paddle vendor/seller id (used in API calls)
    yukassa_shop_id: str = ""
    yukassa_secret_key: str = ""  # HTTP Basic password for the ЮKassa API
    yukassa_webhook_secret: str = ""  # optional shared-secret query param on the webhook URL
    # Prodamus — RU acquirer usable by самозанятый/ИП-НПД/ИП/ООО. Accepts RU cards
    # + SBP AND foreign-issued cards (incl. TR/WW), so it covers WW when Paddle
    # is unavailable to the operator (Paddle blocks RU sellers). 54-ФЗ is handled
    # on Prodamus's side (auto for NPD). `prodamus_payform_url` is the merchant's
    # payform subdomain (e.g. https://yourshop.payform.ru); `prodamus_sys` is the
    # integration code agreed with Prodamus support (required for urlNotification).
    # `prodamus_secret_key` signs checkout requests AND verifies webhook `Sign`.
    prodamus_secret_key: str = ""
    prodamus_payform_url: str = ""  # e.g. https://yourshop.payform.ru (no trailing slash)
    prodamus_sys: str = ""  # integration code from Prodamus support
    # Public origin the provider redirects back to after checkout/portal. In
    # compose this is the Caddy origin (single browser entry point). Trailing
    # slash stripped at use.
    billing_return_origin: str = ""

    # --- Messengers (Telegram first; per-user bots, long polling) ---
    # Base64-encoded 32-byte envelope key (XSalsa20-Poly1305) for
    # ``messengers.bot_token_ciphertext`` / ``byok_enc_blob``. Server-managed —
    # NOT zero-knowledge; the server can decrypt. Empty + hosted = hard boot
    # failure; empty + self-hosted = ephemeral key + warning (bots need
    # re-binding after restart). Generate with EnvelopeCipher.generate_key_b64().
    messenger_token_dek: str = ""
    # Master switch for the long-poll loops. Off → make_envelope returns None,
    # no pollers start, the CRUD endpoints still work (init/bind/delete).
    messenger_long_poll_enabled: bool = True
    # Telegram getUpdates long-poll timeout (seconds) passed to the Bot API.
    messenger_poll_timeout: int = 30
    # TTL for the /start <connect_token> handshake link (seconds).
    messenger_connect_token_ttl_seconds: int = 600

    # --- Dev/test escape hatch ---
    # When true, the legacy self-asserted ``X-User-Id`` header is honored for
    # ``get_current_user_id`` (tests / local dev). NEVER enable in production —
    # it bypasses the verified Principal. Default false.
    auth_allow_insecure_user_header: bool = False

    # --- Rate limiting (I15) ---
    # slowapi storage URI. ``memory://`` (default) is per-process: exact for a
    # single uvicorn worker, but under multiple workers each keeps its own
    # counters so the effective limit is N×the configured rate. Set this to a
    # redis URL (``redis://host:6379``) to share counters across workers when
    # scaling out. The limiter guards brute-force on auth endpoints, invite
    # spam, and runaway /llm/stream cost (per-user).
    ratelimit_storage_uri: str = "memory://"
    # Master switch for the slowapi limiter. When false, ``@limiter.limit``
    # decorators pass through (no 429s) — used by the test suite (which fires
    # many requests from one IP) and as an emergency kill switch. Production
    # leaves it on.
    ratelimit_enabled: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
