"""Boot-time auth configuration validation + public ``AuthConfig`` derivation.

``validate_auth_config(settings)`` runs at startup (``main.create_app``) and raises
``AuthConfigError`` on any matrix violation or missing backend prerequisite, so a
misconfigured deployment refuses to boot instead of silently degrading to mock or
to the insecure header path. ``build_auth_config(settings)`` returns the public
``AuthConfig`` served by ``GET /v1/config`` (no secrets, no per-user data).
"""

from __future__ import annotations

from ai_companion_contracts import (
    AuthBackendKind,
    AuthConfig,
    DeploymentMode,
    FeatureFlags,
    SelfHostedProfile,
)

from ..config import Settings
from .config import allowed_backends


class AuthConfigError(RuntimeError):
    """Raised when the deployment's auth config violates the mode→backend matrix
    or is missing a prerequisite (e.g. OIDC without an issuer). Fail fast at
    boot — never serve requests under a broken auth config."""


def _normalize_mode(mode: str) -> str:
    if mode not in {m.value for m in DeploymentMode}:
        raise AuthConfigError(
            f"DEPLOYMENT_MODE={mode!r} is invalid (expected {[m.value for m in DeploymentMode]})."
        )
    return mode


def _normalize_profile(mode: str, profile: str) -> str | None:
    if mode == DeploymentMode.hosted.value:
        return None
    if profile not in {p.value for p in SelfHostedProfile}:
        raise AuthConfigError(
            f"AUTH_SELF_HOSTED_PROFILE={profile!r} is invalid (expected "
            f"{[p.value for p in SelfHostedProfile]})."
        )
    return profile


def validate_auth_config(settings: Settings) -> tuple[str, str | None, str]:
    """Validate and return ``(mode, profile, backend)``. Raises on any violation."""
    mode = _normalize_mode(settings.deployment_mode)
    profile = _normalize_profile(mode, settings.auth_self_hosted_profile)
    backend = settings.auth_backend

    # I17: the ``X-User-Id`` insecure escape hatch is a full impersonation
    # surface — any request without a verified Principal becomes the header
    # value (or the default user). That is tolerable in self-hosted dev/test
    # but a hard security hole in hosted multi-user mode. Refuse to boot
    # rather than silently serve requests under a spoofable identity.
    if mode == DeploymentMode.hosted.value and settings.auth_allow_insecure_user_header:
        raise AuthConfigError(
            "AUTH_ALLOW_INSECURE_USER_HEADER=1 is forbidden in DEPLOYMENT_MODE=hosted "
            "(it would let any client impersonate any user via the X-User-Id header). "
            "Set it to 0 for hosted deployments."
        )

    permitted = allowed_backends(mode, profile)
    if backend not in permitted:
        raise AuthConfigError(
            f"AUTH_BACKEND={backend!r} is not allowed for "
            f"DEPLOYMENT_MODE={mode}"
            + (f" / AUTH_SELF_HOSTED_PROFILE={profile}" if profile else "")
            + f". Allowed: {sorted(permitted) or '(none for this mode/profile)'}."
        )

    # Per-backend prerequisites.
    if backend == AuthBackendKind.oidc.value:
        if not settings.oidc_issuer or not settings.oidc_client_id:
            raise AuthConfigError("AUTH_BACKEND=oidc requires OIDC_ISSUER and OIDC_CLIENT_ID.")
        if not settings.auth_state_secret:
            raise AuthConfigError(
                "AUTH_BACKEND=oidc requires AUTH_STATE_SECRET (signs the state cookie)."
            )
    elif backend == AuthBackendKind.trusted_header.value:
        if not settings.auth_header_hmac_secret:
            raise AuthConfigError(
                "AUTH_BACKEND=trusted_header requires AUTH_HEADER_HMAC_SECRET "
                "(spoofing guard). Never expose the API directly with this backend."
            )
    elif backend == AuthBackendKind.magic_link.value:
        if not settings.auth_magic_link_secret:
            raise AuthConfigError("AUTH_BACKEND=magic_link requires AUTH_MAGIC_LINK_SECRET.")
        if settings.auth_email_transport == "off":
            raise AuthConfigError("AUTH_BACKEND=magic_link requires AUTH_EMAIL_TRANSPORT != off.")
    # local: no prerequisites (zero-config, zero external deps).

    # Email verification is a local-signup flow: the user proves email
    # ownership by clicking a signed link. OIDC/magic-link already prove it
    # (IdP-verified email / link possession), so the flow is local-only. It
    # also needs a real transport (console/off don't deliver mail) and a
    # signing secret for the sealed token. Refuse to boot rather than silently
    # advertising verification the deployment can't perform ("disclose, don't
    # perform"). The secret falls back to AUTH_MAGIC_LINK_SECRET so an operator
    # who set one secret is covered.
    if settings.feature_email_verification:
        if backend != AuthBackendKind.local.value:
            raise AuthConfigError(
                "FEATURE_EMAIL_VERIFICATION=1 requires AUTH_BACKEND=local "
                "(OIDC/magic-link prove email ownership their own way)."
            )
        if settings.auth_email_transport != "smtp":
            raise AuthConfigError(
                "FEATURE_EMAIL_VERIFICATION=1 requires AUTH_EMAIL_TRANSPORT=smtp "
                "(console/off don't deliver verification mail)."
            )
        if not (settings.auth_email_verification_secret or settings.auth_magic_link_secret):
            raise AuthConfigError(
                "FEATURE_EMAIL_VERIFICATION=1 requires a signing secret: set "
                "AUTH_EMAIL_VERIFICATION_SECRET (or AUTH_MAGIC_LINK_SECRET, used as fallback)."
            )

    # Hosted is multi-user SaaS served over the public internet: the session
    # cookie is only Secure (and thus safe over plaintext hops) when the origin
    # is https. ``auth/sessions.cookie_secure`` already returns False for an
    # http origin, which would mean a non-Secure 14-day session cookie on a
    # hosted deployment — a credential that any MitM on the path can steal.
    # Refuse to boot rather than silently hand out non-Secure session cookies.
    # (Self-hosted may run on http://localhost — the local network is the
    # operator's own threat model.)
    if mode == DeploymentMode.hosted.value and not settings.public_origin.startswith("https://"):
        raise AuthConfigError(
            "DEPLOYMENT_MODE=hosted requires PUBLIC_ORIGIN to be an https:// URL "
            "(the session cookie is only Secure over https; an http origin would "
            "hand out a non-Secure 14-day session cookie). Set PUBLIC_ORIGIN=https://..."
        )

    # Billing is a hosted-only capability (feature_billing and is_hosted in
    # build_auth_config). When it's on, at least one provider must be
    # configured — otherwise checkout 503s for every plan and the UI advertises
    # a purchase flow the deployment can't serve ("disclose, don't perform").
    # Paddle covers WW (USD/EUR); ЮKassa covers RU (RUB); Prodamus covers both
    # (RU cards + SBP AND foreign cards) and is the WW path when the operator
    # can't use Paddle (e.g. RU-resident самозанятый). Secrets are never logged
    # (redaction scrubs `paddle_`/`yukassa_`/`prodamus_` token prefixes).
    if (
        mode == DeploymentMode.hosted.value
        and settings.feature_billing
        and not settings.paddle_api_key
        and not settings.yukassa_shop_id
        and not settings.prodamus_secret_key
    ):
        raise AuthConfigError(
            "DEPLOYMENT_MODE=hosted + FEATURE_BILLING=1 requires at least one "
            "billing provider configured: PADDLE_API_KEY (WW), "
            "YUKASSA_SHOP_ID + YUKASSA_SECRET_KEY (RU), or "
            "PRODAMUS_SECRET_KEY + PRODAMUS_PAYFORM_URL + PRODAMUS_SYS (WW+RU). "
            "Set them in .env."
        )

    return mode, profile, backend


def build_auth_config(settings: Settings) -> AuthConfig:
    """Derive the public ``AuthConfig`` (GET /v1/config). Assumes the config has
    already been validated at boot; still degrades safely if not."""
    mode, profile, backend = validate_auth_config(settings)

    is_hosted = mode == DeploymentMode.hosted.value
    features = FeatureFlags(
        billing=settings.feature_billing and is_hosted,
        credits=settings.feature_credits and is_hosted,
        hosted_fallback=settings.feature_hosted_fallback and is_hosted,
        magic_links=(backend == AuthBackendKind.magic_link.value)
        or (settings.feature_magic_links and is_hosted),
        # Email verification is local-only; the flag is the single switch (no
        # hosted gating — a self-hosted operator may want it too).
        email_verification=settings.feature_email_verification,
        journal=True,
        shares=True,
    )

    return AuthConfig(
        mode=DeploymentMode(mode),
        profile=SelfHostedProfile(profile) if profile else None,
        auth_backends=[AuthBackendKind(backend)],
        features=features,
    )


__all__ = ["AuthConfigError", "build_auth_config", "validate_auth_config"]
