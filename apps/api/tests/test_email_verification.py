"""Email verification for local-account signup — soft, feature-flagged flow.

Covers the full path: flag on ⇒ signup creates an unverified account + emails
a sealed-token link; the GET click-through verifies it; resend is
non-enumerating; flag off ⇒ behavior identical to today (verified at signup,
endpoints 404). Plus the bootstrap validation matrix for the flag and a
redaction check that the sealed token never reaches logs.

The email transport is monkey-patched to a capture transport (same pattern as
``test_family_invites.py``) so we can read the link that would otherwise be
mailed. Real auth is exercised end-to-end (``make_app`` with the insecure
header escape hatch OFF); the verification token is sealed with the same
``seal`` helper the API uses.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlsplit

import pytest

from ai_companion_api.auth.backends import magic_link as ml
from ai_companion_api.auth.backends.magic_link import ConsoleEmailTransport
from ai_companion_api.auth.bootstrap import AuthConfigError, validate_auth_config
from ai_companion_api.config import Settings
from ai_companion_api.observability.redaction import RedactingFilter

VERIFY_SECRET = "test-verify-secret-fixed"


def _settings(monkeypatch, **env) -> Settings:
    """Build Settings directly for bootstrap matrix tests (mirrors
    test_auth_bootstrap._settings)."""
    env.setdefault("AUTH_ALLOW_INSECURE_USER_HEADER", "0")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    return Settings()


class _CapturingTransport:
    """Records every send() call so the test can read the link + recipient."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, *, to: str, link: str, subject: str | None = None) -> None:
        self.sent.append({"to": to, "link": link, "subject": subject})


@pytest.fixture
def make_verify_app(make_app, monkeypatch):
    """make_app variant with FEATURE_EMAIL_VERIFICATION on + a pinned secret +
    a capture transport installed via the magic_link module (so
    email_verification.send_verification_email picks it up)."""
    monkeypatch.setenv("AUTH_EMAIL_VERIFICATION_SECRET", VERIFY_SECRET)

    def _make(**env):
        env.setdefault("FEATURE_EMAIL_VERIFICATION", "1")
        env.setdefault("AUTH_EMAIL_TRANSPORT", "smtp")
        env.setdefault("AUTH_EMAIL_VERIFICATION_SECRET", VERIFY_SECRET)
        return make_app(**env)

    return _make


def _install_capture() -> _CapturingTransport:
    cap = _CapturingTransport()
    real = ml.default_transport

    def _patched(_settings):  # noqa: ANN001
        return cap

    ml.default_transport = _patched  # type: ignore[assignment]
    # Stash the real fn on the capture so the test can restore it.
    cap._real = real  # type: ignore[attr-defined]
    return cap


def _restore(cap: _CapturingTransport) -> None:
    ml.default_transport = cap._real  # type: ignore[attr-defined]


def _token_from_link(link: str) -> str:
    qs = parse_qs(urlsplit(link).query)
    return qs["token"][0]


# --- signup → email → verify -----------------------------------------------


async def test_signup_unverified_and_emails_link(make_verify_app, app_client) -> None:
    cap = _install_capture()
    try:
        app = make_verify_app()
        async with app_client(app) as ac:
            r = await ac.post(
                "/v1/auth/signup",
                json={"email": "Alice@Example.com", "password": "pwaaaaaaaaaa"},
            )
            assert r.status_code == 200, r.text
            principal = r.json()
            # Soft flow: the session IS issued (cookie set) but the account is unverified.
            assert principal["email_verified"] is False
            assert "retellis_sess" in ac.cookies

            # A verification email was captured, addressed to the normalized email.
            assert len(cap.sent) == 1, "verification email was not sent"
            sent = cap.sent[0]
            assert sent["to"] == "alice@example.com"
            assert "/v1/auth/verify-email?token=" in sent["link"]
            # The subject is the verification subject (not the magic-link default).
            assert sent["subject"] == "Confirm your Retellis email"

            # /me reflects the unverified state.
            me = await ac.get("/v1/auth/me")
            assert me.json()["email_verified"] is False
    finally:
        _restore(cap)


async def test_verify_email_link_flips_flag_and_redirects_home(make_verify_app, app_client) -> None:
    cap = _install_capture()
    try:
        app = make_verify_app()
        async with app_client(app) as ac:
            await ac.post(
                "/v1/auth/signup",
                json={"email": "alice@example.com", "password": "pwaaaaaaaaaa"},
            )
            token = _token_from_link(cap.sent[0]["link"])

            # The click-through is a GET that 303-redirects home. No session is
            # required (it's a public email link); it flips the flag by the
            # email encoded in the sealed token.
            r = await ac.get(f"/v1/auth/verify-email?token={token}", follow_redirects=False)
            assert r.status_code == 303
            assert r.headers["location"].rstrip("/") == app.state.settings.public_origin.rstrip("/")

            # The flag is now flipped — /me (using the signup session) sees verified.
            me = await ac.get("/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["email_verified"] is True
    finally:
        _restore(cap)


async def test_invalid_token_redirects_to_failed(make_verify_app, app_client) -> None:
    app = make_verify_app()
    async with app_client(app) as ac:
        # A tampered / garbage token → 303 to /?verify=failed, no verification.
        r = await ac.get("/v1/auth/verify-email?token=garbage.garbage", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].endswith("/?verify=failed")


async def test_expired_token_redirects_to_failed(monkeypatch, make_verify_app, app_client) -> None:
    # Seal a token that is already expired, using the pinned secret.
    import time

    from ai_companion_api.auth.sessions import seal

    payload = {
        "email": "alice@example.com",
        "exp": int(time.time()) - 1,  # expired a second ago
        "nonce": "x",
    }
    token = seal(payload, VERIFY_SECRET)
    app = make_verify_app()
    async with app_client(app) as ac:
        r = await ac.get(f"/v1/auth/verify-email?token={token}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].endswith("/?verify=failed")


# --- resend (non-enumerating) ---------------------------------------------


async def test_resend_is_non_enumerating(make_verify_app, app_client) -> None:
    cap = _install_capture()
    try:
        app = make_verify_app()
        async with app_client(app) as ac:
            # Unknown email → ack ok, nothing sent.
            r = await ac.post(
                "/v1/auth/verify-email/resend", json={"email": "ghost@example.com"}
            )
            assert r.status_code == 200
            assert r.json() == {"ok": True}
            assert len(cap.sent) == 0

            # Signup an unverified user, then resend → a second link is sent.
            await ac.post(
                "/v1/auth/signup",
                json={"email": "alice@example.com", "password": "pwaaaaaaaaaa"},
            )
            assert len(cap.sent) == 1
            r2 = await ac.post(
                "/v1/auth/verify-email/resend", json={"email": "alice@example.com"}
            )
            assert r2.status_code == 200
            assert r2.json() == {"ok": True}
            assert len(cap.sent) == 2
            assert cap.sent[1]["to"] == "alice@example.com"
    finally:
        _restore(cap)


# --- flag off → identical to today ----------------------------------------


async def test_flag_off_signup_verified_and_endpoints_404(make_app, app_client) -> None:
    # make_app defaults: FEATURE_EMAIL_VERIFICATION unset → off, transport console.
    app = make_app()
    async with app_client(app) as ac:
        r = await ac.post(
            "/v1/auth/signup",
            json={"email": "alice@example.com", "password": "pwaaaaaaaaaa"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["email_verified"] is True  # trusted immediately (no flag)

        # The verify endpoints are disabled (404) when the flag is off.
        assert (
            await ac.get("/v1/auth/verify-email?token=anything", follow_redirects=False)
        ).status_code == 404
        assert (
            await ac.post("/v1/auth/verify-email/resend", json={"email": "alice@example.com"})
        ).status_code == 404


# --- bootstrap matrix -----------------------------------------------------


def test_bootstrap_flag_on_requires_smtp_not_console(monkeypatch) -> None:
    s = _settings(
        monkeypatch,
        FEATURE_EMAIL_VERIFICATION="1",
        AUTH_BACKEND="local",
        AUTH_EMAIL_TRANSPORT="console",
        AUTH_EMAIL_VERIFICATION_SECRET=VERIFY_SECRET,
    )
    with pytest.raises(AuthConfigError, match="smtp"):
        validate_auth_config(s)


def test_bootstrap_flag_on_requires_smtp_not_off(monkeypatch) -> None:
    s = _settings(
        monkeypatch,
        FEATURE_EMAIL_VERIFICATION="1",
        AUTH_BACKEND="local",
        AUTH_EMAIL_TRANSPORT="off",
        AUTH_EMAIL_VERIFICATION_SECRET=VERIFY_SECRET,
    )
    with pytest.raises(AuthConfigError, match="smtp"):
        validate_auth_config(s)


def test_bootstrap_flag_on_requires_secret(monkeypatch) -> None:
    # No verification secret AND no magic-link secret to fall back to.
    s = _settings(
        monkeypatch,
        FEATURE_EMAIL_VERIFICATION="1",
        AUTH_BACKEND="local",
        AUTH_EMAIL_TRANSPORT="smtp",
        AUTH_MAGIC_LINK_SECRET="",
        AUTH_EMAIL_VERIFICATION_SECRET="",
    )
    with pytest.raises(AuthConfigError, match="secret"):
        validate_auth_config(s)


def test_bootstrap_flag_on_accepts_magic_link_secret_fallback(monkeypatch) -> None:
    # No dedicated verification secret, but AUTH_MAGIC_LINK_SECRET is set → OK.
    s = _settings(
        monkeypatch,
        FEATURE_EMAIL_VERIFICATION="1",
        AUTH_BACKEND="local",
        AUTH_EMAIL_TRANSPORT="smtp",
        AUTH_MAGIC_LINK_SECRET="shared-secret",
        AUTH_EMAIL_VERIFICATION_SECRET="",
    )
    mode, profile, backend = validate_auth_config(s)
    assert backend == "local"


def test_bootstrap_flag_on_requires_local_backend(monkeypatch) -> None:
    s = _settings(
        monkeypatch,
        DEPLOYMENT_MODE="self_hosted",
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp.example",
        OIDC_CLIENT_ID="c",
        AUTH_STATE_SECRET="s",
        FEATURE_EMAIL_VERIFICATION="1",
        AUTH_EMAIL_TRANSPORT="smtp",
        AUTH_EMAIL_VERIFICATION_SECRET=VERIFY_SECRET,
    )
    with pytest.raises(AuthConfigError, match="local"):
        validate_auth_config(s)


def test_bootstrap_flag_off_needs_no_transport_or_secret(monkeypatch) -> None:
    # Flag off → no verification prerequisites; plain local default boots fine.
    s = _settings(monkeypatch)
    mode, profile, backend = validate_auth_config(s)
    assert backend == "local"


# --- redaction: the sealed token never reaches logs ----------------------


async def test_console_transport_does_not_log_verification_token(caplog) -> None:
    """The verification link carries the sealed token (no sk-/Bearer shape, so
    redaction wouldn't catch it). ConsoleEmailTransport must log only the
    recipient — never the link — exactly like the magic-link transport."""
    token = "SECRETVERIFYVALUE1234567890"
    link = f"https://app.example.com/v1/auth/verify-email?token={token}"
    transport = ConsoleEmailTransport()
    logger = logging.getLogger("ai_companion_api.auth.backends.magic_link")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="ai_companion_api.auth.backends.magic_link"):
        await transport.send(to="user@example.com", link=link, subject="Confirm your Retellis email")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in joined
    assert "token=" not in joined
    # The recipient IS logged (operators can see a verification was issued).
    assert "user@example.com" in joined


# --- SMTP STARTTLS policy --------------------------------------------------


class _FakeSMTP:
    """Records method calls so the STARTTLS-policy tests can assert which path
    the transport took, without a real socket."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def has_extn(self, name: str) -> bool:
        return True

    def starttls(self, context: object | None = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")

    def send_message(self, msg: object) -> None:
        self.calls.append("send_message")


def _smtp_settings(monkeypatch, *, starttls: str) -> Settings:
    return _settings(
        monkeypatch,
        SMTP_HOST="postfix",
        SMTP_PORT="25",
        SMTP_USERNAME="",
        SMTP_PASSWORD="",
        SMTP_FROM="noreply@retellis.com",
        SMTP_STARTTLS=starttls,
    )


async def test_smtp_starttls_never_skips_starttls(monkeypatch) -> None:
    """An internal postfix relay on port 25 has no TLS cert — ``SMTP_STARTTLS=
    never`` must use plain SMTP (no STARTTLS, no login when no username)."""
    from ai_companion_api.auth.backends import magic_link as ml
    from ai_companion_api.auth.backends.magic_link import SMTPEmailTransport

    fake = _FakeSMTP()
    monkeypatch.setattr(ml.smtplib, "SMTP", lambda host, port, timeout=15: fake)
    transport = SMTPEmailTransport(_smtp_settings(monkeypatch, starttls="never"))
    await transport.send(to="u@x.com", link="https://x/v1/auth/verify-email?token=t")
    assert "starttls" not in fake.calls
    assert "login" not in fake.calls
    assert "send_message" in fake.calls


async def test_smtp_starttls_required_upgrades(monkeypatch) -> None:
    """Default ``required`` keeps the secure external-SMTP behavior: STARTTLS
    is always attempted before send."""
    from ai_companion_api.auth.backends import magic_link as ml
    from ai_companion_api.auth.backends.magic_link import SMTPEmailTransport

    fake = _FakeSMTP()
    monkeypatch.setattr(ml.smtplib, "SMTP", lambda host, port, timeout=15: fake)
    transport = SMTPEmailTransport(_smtp_settings(monkeypatch, starttls="required"))
    await transport.send(to="u@x.com", link="https://x/v1/auth/verify-email?token=t")
    assert "starttls" in fake.calls
    assert "send_message" in fake.calls