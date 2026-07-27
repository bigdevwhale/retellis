"""Family-invite signing secret — auto-generated + persisted on first boot.

The family-invite tokens are HMAC-SHA256 signed by
``auth.sessions.seal``/``open_sealed``. ``open_sealed`` rejects any token
signed with an empty secret, so the family-invite flow is dead-on-arrival
when neither ``AUTH_INVITE_SECRET`` nor ``AUTH_STATE_SECRET`` is set in the
deployment env (the default for the local ``self_hosted+local`` profile).

This test exercises the new ``auth.invite_secret.ensure_invite_secret``
bootstrap: it must (a) generate + persist a fresh secret when none is set,
(b) reuse the persisted secret on the next call, and (c) yield to an
explicit ``settings.auth_invite_secret`` so an operator can override.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from ai_companion_api.auth.invite_secret import ensure_invite_secret
from ai_companion_api.auth.sessions import open_sealed, seal


class _S(BaseModel):
    auth_invite_secret: str = ""
    auth_state_secret: str = ""


@pytest.fixture
def secret_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    f = tmp_path / "invite_secret"
    monkeypatch.setenv("COMPANION_INVITE_SECRET_FILE", str(f))
    return f


def test_generates_and_persists_when_no_secret(
    secret_file: Path,
) -> None:
    s = _S()
    val = ensure_invite_secret(settings=s)
    assert val, "expected a non-empty generated secret"
    assert s.auth_invite_secret == val, "must write back to settings"
    assert secret_file.exists()
    # On Linux, the file must be 0600 — the API user can read it, no one
    # else. Windows ignores POSIX mode bits (no enforcement), so we only
    # check on platforms where it actually applies. In Docker (Linux) this
    # is the security boundary that keeps the signing key off the volume
    # for other users on the host.
    if sys.platform != "win32":
        import stat

        mode = stat.S_IMODE(secret_file.stat().st_mode)
        assert mode == stat.S_IRUSR | stat.S_IWUSR, f"file must be 0600, got {oct(mode)}"
    # The persisted value round-trips into ``seal``/``open_sealed``.
    tok = seal({"family_id": "f1", "exp": 9_999_999_999, "jti": "j", "nonce": "n"}, val)
    assert open_sealed(tok, val) is not None


def test_reuses_persisted_secret_on_subsequent_boot(
    secret_file: Path,
) -> None:
    s1 = _S()
    v1 = ensure_invite_secret(settings=s1)
    # Simulate a process restart: fresh settings, same on-disk file.
    s2 = _S()
    v2 = ensure_invite_secret(settings=s2)
    assert v1 == v2, "second boot MUST reuse the persisted secret"
    # Outstanding tokens issued under v1 still verify under v2.
    tok = seal({"family_id": "f1", "exp": 9_999_999_999, "jti": "j", "nonce": "n"}, v1)
    assert open_sealed(tok, v2) is not None


def test_explicit_auth_invite_secret_wins(
    secret_file: Path,
) -> None:
    s = _S(auth_invite_secret="operator-chosen-secret-xyz")
    val = ensure_invite_secret(settings=s)
    assert val == "operator-chosen-secret-xyz"
    # And the persisted file is NOT created when an explicit secret is set —
    # we don't want a stale on-disk file shadowing a future operator override.
    assert not secret_file.exists()


def test_explicit_auth_state_secret_falls_through(
    secret_file: Path,
) -> None:
    s = _S(auth_state_secret="state-secret-abc")
    val = ensure_invite_secret(settings=s)
    assert val == "state-secret-abc"
    assert not secret_file.exists()


def test_persisted_secret_verifies_sealed_token(
    secret_file: Path,
) -> None:
    """End-to-end: a token sealed with the persisted secret can be opened
    by a fresh process that reads the same file. This is the exact path
    the family accept endpoint exercises in production."""
    s1 = _S()
    v1 = ensure_invite_secret(settings=s1)
    tok = seal(
        {
            "family_id": "ceac9fff31804e77ab7395c437229e72",
            "email": "x@example.com",
            "role": "member",
            "exp": 9_999_999_999,
            "jti": "j",
            "nonce": "n",
        },
        v1,
    )
    s2 = _S()
    v2 = ensure_invite_secret(settings=s2)
    payload = open_sealed(tok, v2)
    assert payload is not None
    assert payload["family_id"] == "ceac9fff31804e77ab7395c437229e72"
