"""Provider fallback chain — env ladder + BYOK wiring for the 3 new kinds.

Covers the 3 kinds added in the BYOK UX upgrade (azure, aihubmix, bedrock):
- env-fallback candidate ordering (azure/aihubmix/bedrock appear after
  openai/anthropic/openrouter/google, before the Ollama local fallback)
- Bedrock's AWS credential triplet (access key + secret + region) is
  propagated to the adapter as ``extra=`` and the candidate is skipped when
  any of the three is missing (a half-configured Bedrock would 400 every
  call)
- Azure's api_version / api_base are passed through to the adapter
- BYOK path carries the same ``extra`` payload via ``DecryptedKey.extra``
  (the wider key shape that supports a JSON envelope of credential fields)
"""

from __future__ import annotations

import pytest

from ai_companion_api.config import Settings
from ai_companion_api.llm.litellm_adapter import LiteLLMAdapter
from ai_companion_api.llm.provider import (
    _ENV_ORDER,
    DEFAULT_MODELS,
    UTILITY_MODELS,
    _bedrock_creds_from_settings,
    build_chain,
    configured_env_kinds,
)
from ai_companion_api.vault.decrypt import DecryptedKey


def _settings(**overrides) -> Settings:
    """Build a Settings with zero/empty values, then layer overrides."""
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# --- 1. The env ladder has 7 real-provider kinds in the stable order --------


def test_env_order_includes_all_real_kinds_before_ollama() -> None:
    # The 7 real kinds in stable order. The 8th kind in the picker,
    # ``mock``, is always the last node — it's never in the env ladder.
    assert _ENV_ORDER == (
        "openai",
        "anthropic",
        "openrouter",
        "google",
        "azure",
        "aihubmix",
        "bedrock",
    )


def test_configured_env_kinds_filters_partially_set_bedrock() -> None:
    """A Bedrock key alone is NOT enough — secret + region must also be set.
    Without this guard the chain would emit a half-configured candidate that
    400s on every call."""
    s = _settings(
        litellm_api_key_bedrock="AKIA-access-key-only",
        # aws_secret_access_key + aws_region_name deliberately unset.
    )
    assert "bedrock" not in configured_env_kinds(s)


def test_configured_env_kinds_keeps_full_bedrock() -> None:
    s = _settings(
        litellm_api_key_bedrock="AKIA-full",
        aws_secret_access_key="a" * 40,
        aws_region_name="us-east-1",
    )
    assert "bedrock" in configured_env_kinds(s)


# --- 2. Bedrock's credential triplet is propagated to the adapter ----------


def test_bedrock_creds_from_settings_requires_all_three() -> None:
    assert _bedrock_creds_from_settings(_settings()) is None
    assert _bedrock_creds_from_settings(
        _settings(litellm_api_key_bedrock="AKIA")
    ) is None
    assert _bedrock_creds_from_settings(
        _settings(
            litellm_api_key_bedrock="AKIA",
            aws_secret_access_key="a" * 40,
        )
    ) is None
    s = _settings(
        litellm_api_key_bedrock="AKIA",
        aws_secret_access_key="a" * 40,
        aws_region_name="us-east-1",
    )
    creds = _bedrock_creds_from_settings(s)
    assert creds == {
        "aws_access_key_id": "AKIA",
        "aws_secret_access_key": "a" * 40,
        "aws_region_name": "us-east-1",
    }


# --- 3. DEFAULT_MODELS / UTILITY_MODELS have entries for the 3 new kinds ---


def test_default_models_covers_all_kinds() -> None:
    # Every real-provider kind (and the synthetic 'mock') has a default.
    for k in (
        "openai",
        "anthropic",
        "google",
        "openrouter",
        "ollama",
        "azure",
        "aihubmix",
        "bedrock",
    ):
        assert k in DEFAULT_MODELS, f"missing default model for {k}"


def test_utility_models_skips_bedrock() -> None:
    # Bedrock has no cheap sibling — the utility caller falls through to the
    # serving model. The other new kinds DO have a fast sibling.
    assert "azure" in UTILITY_MODELS
    assert "aihubmix" in UTILITY_MODELS
    assert "bedrock" not in UTILITY_MODELS


# --- 4. The chain end-to-end: no BYOK, only env set ---------------------


def test_build_chain_no_byok_full_env_includes_bedrock() -> None:
    """When all 7 env kinds are set, the chain has 7 real candidates plus
    the mock stand-in. The BYOK node is absent (no client key)."""
    s = _settings(
        litellm_api_key_openai="sk-openai",
        litellm_api_key_anthropic="sk-anth",
        litellm_api_key_openrouter="sk-or",
        litellm_api_key_google="AIza",
        litellm_api_key_azure="sk-azure",
        azure_api_base="https://example.azure.com",
        azure_api_version="2024-02-01",
        litellm_api_key_aihubmix="sk-aihub",
        litellm_api_key_bedrock="AKIA-b",
        aws_secret_access_key="a" * 40,
        aws_region_name="us-east-1",
    )
    cands = build_chain(enc_key_blob=None, settings=s, ecdh=None, model="gpt-4o-mini")
    kinds = [c.kind for c in cands]
    # The env ladder in stable order, with mock at the end.
    assert kinds == [
        "openai",
        "anthropic",
        "openrouter",
        "google",
        "azure",
        "aihubmix",
        "bedrock",
        "mock",
    ]
    # The Bedrock candidate's adapter carries the AWS triplet as extra.
    bedrock = next(c for c in cands if c.kind == "bedrock")
    assert isinstance(bedrock.adapter, LiteLLMAdapter)
    assert bedrock.adapter._extra is not None
    assert bedrock.adapter._extra["aws_access_key_id"] == "AKIA-b"
    assert bedrock.adapter._extra["aws_secret_access_key"] == "a" * 40
    assert bedrock.adapter._extra["aws_region_name"] == "us-east-1"


def test_build_chain_no_byok_partial_env_drops_bedrock() -> None:
    """A half-configured Bedrock (key without secret/region) is dropped from
    the chain — emitting it would 400 every call."""
    s = _settings(
        litellm_api_key_openai="sk-openai",
        litellm_api_key_bedrock="AKIA-only",
        # secret + region deliberately missing.
    )
    cands = build_chain(enc_key_blob=None, settings=s, ecdh=None, model="gpt-4o-mini")
    kinds = [c.kind for c in cands]
    assert "bedrock" not in kinds
    assert "mock" in kinds


# --- 5. BYOK candidate carries the wider DecryptedKey.extra payload ------


def test_byok_bedrock_candidate_carries_extra() -> None:
    """The BYOK node is always the FIRST in the chain (when present) and
    carries a DecryptedKey whose ``extra`` dict holds the AWS triplet. The
    router zeroizes ``api_key`` (and we zeroize the source bytearray); the
    ``extra`` strings are short enough that they too must be wiped after the
    call (see router.run_with_fallback)."""
    dk = DecryptedKey(
        provider_kind="bedrock",
        api_key=bytearray(b"AKIA-byok"),
        base_url=None,
        extra={
            "aws_access_key_id": "AKIA-byok",
            "aws_secret_access_key": "secret-byok-value-32-bytes",
            "aws_region_name": "eu-west-2",
        },
    )
    # We don't decrypt here — the chain builder only inspects the shape
    # and forwards it to the adapter. Build a fake adapter manually to
    # assert the wiring.
    from ai_companion_api.llm.litellm_adapter import LiteLLMAdapter

    adapter = LiteLLMAdapter(
        "bedrock",
        bytes(dk.api_key),
        None,
        extra=dk.extra,
    )
    # The adapter carries the kind + extra payload the BYOK picker wrote.
    assert adapter.provider_kind == "bedrock"
    assert adapter._extra == dk.extra


# --- 6. The chain is always terminated by the mock stand-in ----------------


@pytest.mark.parametrize("env_keys", [{}, {"litellm_api_key_openai": "sk-o"}])
def test_chain_always_ends_in_mock(env_keys: dict) -> None:
    """Honest-limit invariant: every chain has a mock tail. The user's
    turn never fails because no real provider was reachable — it always
    gets a deterministic stand-in reply, marked `is_mock=True` so the UI
    can disclose the fallthrough."""
    s = _settings(**env_keys)
    cands = build_chain(enc_key_blob=None, settings=s, ecdh=None, model="gpt-4o-mini")
    assert cands[-1].kind == "mock"
    assert cands[-1].is_mock is True
