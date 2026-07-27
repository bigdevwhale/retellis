r"""Provider resolution + the fallback chain — the security-critical precedence:

    BYOK enc_key_blob  →  LITELLM_API_KEY_<KIND> env  →  Ollama (local)  →  mock

``build_chain`` returns the ordered list of ``RoutingCandidate`` objects for a
turn, ending in the mock stand-in so a turn always completes. BYOK wins even if
env keys are present; the env ladder is tried in a stable order; Ollama is a
last-resort local node (no key, ``base_url`` only) added when
``ollama_base_url`` is configured; mock is always last.

The decrypted BYOK key is held on the BYOK candidate's ``decrypted`` field as a
``DecryptedKey`` whose ``api_key`` bytearray the router zeroizes *after* the
chain run completes (even if BYOK failed and a later candidate served the turn).
Env keys are server-configured ``str`` and are not zeroized (they live in
settings for the process lifetime). Mock never holds a key.

``resolve_provider`` (Phase 2 single-shot resolution) remains as a thin wrapper
over ``build_chain`` for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..vault.decrypt import DecryptedKey, DecryptError, decrypt_key_blob
from ..vault.session_ecdh import SessionECDH
from .litellm_adapter import LiteLLMAdapter
from .mock_adapter import MockAdapter
from .types import LlmAdapter

# Default model per provider kind. For OpenRouter/Ollama the litellm model string
# encodes the upstream; base_url can override the endpoint. Azure uses a
# deployment name (no upstream model prefix) — leaving it blank forces the user
# to pick one. Bedrock uses litellm's `bedrock/<model-id>` convention.
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "google": "gemini/gemini-1.5-flash",
    "openrouter": "openrouter/anthropic/claude-3.5-haiku",
    "ollama": "ollama/llama3.3",
    # Azure: the user supplies the deployment name in the per-request model
    # field; ``azure/<deployment>`` is what litellm expects. We keep a sensible
    # default so an empty user pick still routes.
    "azure": "azure/gpt-4o-mini",
    # AIHubMix is OpenAI-compatible; default to a cheap sibling and let the
    # user override per turn.
    "aihubmix": "gpt-4o-mini",
    # Bedrock — match the same cheap-sibling the per-kind utility uses so the
    # chat turn on a default-pick provider doesn't land on a 200k-context
    # Sonnet out of the box.
    "bedrock": "bedrock/anthropic.claude-3-5-haiku-20241022-v1:0",
    "mock": "mock",
}

# P2: cheap sibling per provider kind for UTILITY calls (salience judge — a
# simple classification task that doesn't need the user's flagship chat model).
# Kinds without a known-safe cheap sibling (ollama: the model must exist
# locally; bedrock: the model id carries the region into the model string)
# fall back to the serving model. Same key, different model id.
UTILITY_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "google": "gemini/gemini-1.5-flash",
    "openrouter": "openrouter/anthropic/claude-3.5-haiku",
    "azure": "azure/gpt-4o-mini",
    "aihubmix": "gpt-4o-mini",
}


def utility_model_for(kind: str, serving_model: str, *, override: str | None = None) -> str:
    """The model id for a post-turn utility call (judge). ``override`` is the
    operator's ``UTILITY_MODEL`` setting; else the kind's cheap sibling; else
    the serving model. Extraction/consolidation stay on the serving model —
    they produce user-visible memory content and deserve the better model."""
    if override:
        return override
    return UTILITY_MODELS.get(kind, serving_model)

# Env var name per kind on Settings.
_ENV_ATTR: dict[str, str] = {
    "openai": "litellm_api_key_openai",
    "anthropic": "litellm_api_key_anthropic",
    "openrouter": "litellm_api_key_openrouter",
    "google": "litellm_api_key_google",
    "azure": "litellm_api_key_azure",
    "aihubmix": "litellm_api_key_aihubmix",
    "bedrock": "litellm_api_key_bedrock",
}

# Stable order in which env providers are tried as fallbacks. OpenAI-family
# providers first (most general, env keys more common), then Anthropic /
# Google, then OpenAI-compatible aggregators (Azure, AIHubMix), then Bedrock
# (needs the full AWS triplet, often unset on dev machines).
_ENV_ORDER: tuple[str, ...] = (
    "openai",
    "anthropic",
    "openrouter",
    "google",
    "azure",
    "aihubmix",
    "bedrock",
)


@dataclass
class RoutingCandidate:
    """One node in the fallback chain for a turn."""

    kind: str
    model: str
    base_url: str | None
    adapter: LlmAdapter
    is_mock: bool
    decrypted: DecryptedKey | None  # only the BYOK candidate carries a key to zeroize


@dataclass
class ResolvedProvider:
    """Phase 2 single-shot resolution view (first candidate of the chain)."""

    adapter: LlmAdapter
    model: str
    kind: str
    is_mock: bool
    decrypted: DecryptedKey | None  # router zeroizes .api_key after the call


class ProviderResolutionError(Exception):
    """Raised when the BYOK blob is present but undecryptable. Redacted message."""


def _env_key(settings: Settings, kind: str) -> str | None:
    attr = _ENV_ATTR.get(kind)
    if not attr:
        return None
    val = getattr(settings, attr, "") or ""
    # Bedrock's "key" alone is not enough — lite-llm's signature requires
    # the full AWS triplet (access key + secret + region). Half-configured
    # Bedrock would 400 every call; gate at the env-key lookup so a
    # partial triplet doesn't reach the candidate builder and raise.
    if kind == "bedrock" and not _bedrock_creds_from_settings(settings):
        return None
    return val or None


def _bedrock_creds_from_settings(settings: Settings) -> dict[str, str] | None:
    """AWS credential triplet for a Bedrock candidate (env-fallback path).

    Returns None when not all three are configured — the candidate is then
    skipped (we never want a half-configured Bedrock candidate in the chain
    because lite-llm's signature would 400 every call).
    """
    ak = settings.litellm_api_key_bedrock
    sk = settings.aws_secret_access_key
    region = settings.aws_region_name
    if not (ak and sk and region):
        return None
    return {
        "aws_access_key_id": ak,
        "aws_secret_access_key": sk,
        "aws_region_name": region,
    }


def _env_candidate(*, kind: str, key: str, settings: Settings) -> RoutingCandidate:
    """Build a RoutingCandidate for an env-fallback provider.

    Most kinds are a vanilla ``LiteLLMAdapter(kind, key, base_url=None)``; the
    exceptions are Azure (which needs ``azure_api_base`` and ``azure_api_version``)
    and Bedrock (which needs an AWS triplet instead of a Bearer key). The
    Azure/Bedrock adapters receive the extra payload via ``extra=`` so the
    adapter can pick the right litellm kwargs.
    """
    base_url: str | None = None
    extra: dict[str, str] | None = None
    if kind == "azure":
        base_url = settings.azure_api_base or None
        if settings.azure_api_version:
            extra = {"api_version": settings.azure_api_version}
    elif kind == "bedrock":
        creds = _bedrock_creds_from_settings(settings)
        if not creds:
            # Should be unreachable — _env_key only returns the key when the
            # primary field is set, but the rest of the triplet may be empty.
            # In that case the candidate would always 400; skip it.
            raise ProviderResolutionError(
                "bedrock env candidate incomplete: aws_secret_access_key "
                "and aws_region_name are required alongside the access key"
            )
        extra = creds
    return RoutingCandidate(
        kind=kind,
        model=DEFAULT_MODELS[kind],
        base_url=base_url,
        adapter=LiteLLMAdapter(kind, key, base_url, extra=extra),
        is_mock=False,
        decrypted=None,
    )


def configured_env_kinds(settings: Settings) -> list[str]:
    """Env-configured provider kinds, in the stable fallback order."""
    return [kind for kind in _ENV_ORDER if _env_key(settings, kind)]


def build_chain(
    *,
    enc_key_blob: str | None,
    settings: Settings,
    ecdh: SessionECDH,
    model: str | None = None,
    byok_decrypted: DecryptedKey | None = None,
) -> list[RoutingCandidate]:
    """Build the ordered fallback chain for one turn (always ends in mock).

    ``model`` is the user-selected model id for the BYOK provider (from
    ``LlmStreamRequest.model`` / ``Provider.model``). When set, the BYOK
    candidate uses it instead of the server default for the kind; env-fallback
    and Ollama candidates keep their defaults (those are server-configured).

    BYOK resolution is ADDITIVE: the per-turn ECDH-sealed ``enc_key_blob`` path
    stays the primary source (existing clients + tests). When the client sends
    no per-turn blob (``enc_key_blob is None``) but the server has resolved a
    ``DecryptedKey`` from its envelope store (``providers.api_key_ciphertext``),
    the caller passes it as ``byok_decrypted`` and the chain uses it directly —
    skipping the ECDH decrypt. The two sources are mutually exclusive: a non-null
    ``enc_key_blob`` wins (back-comat with the per-turn re-seal path).
    """
    cands: list[RoutingCandidate] = []
    byok_kind: str | None = None

    # 1) BYOK wins. Per-turn ECDH blob is the primary; envelope-resolved
    #    ``byok_decrypted`` is the fallback when the client sends no blob.
    if enc_key_blob:
        try:
            dk = decrypt_key_blob(enc_key_blob, ecdh.private_key)
        except DecryptError as exc:
            raise ProviderResolutionError(str(exc)) from exc
    else:
        dk = byok_decrypted

    if dk is not None:
        kind = dk.provider_kind
        byok_kind = kind
        byok_model = model or DEFAULT_MODELS.get(kind, DEFAULT_MODELS["openai"])
        base_url = dk.base_url
        if kind == "ollama":
            # Local Ollama runs at localhost with no key; Ollama Cloud is a
            # hosted endpoint that needs a Bearer api_key. LiteLLM's native
            # ``ollama/`` provider is built for local Ollama — it doesn't
            # reliably send the Bearer key or hit the right path on the hosted
            # endpoint (surfaces as ``APIConnectionError``). So route Cloud
            # through its OpenAI-compatible endpoint (``{base_url}/v1`` with the
            # ``openai/`` model prefix), which LiteLLM handles cleanly with
            # ``api_key`` as a Bearer token. Keep native ``ollama/`` for a local
            # endpoint. (``provider_kind`` stays "ollama" for usage/routing
            # display; LiteLLM routes by the model prefix, not this field.)
            if not base_url:
                base_url = settings.ollama_base_url or None
            bare = byok_model.removeprefix("ollama/") if byok_model else byok_model
            is_local = not base_url or any(
                h in base_url for h in ("localhost", "127.0.0.1", "0.0.0.0")
            )
            if is_local:
                if bare and not bare.startswith("ollama/"):
                    byok_model = f"ollama/{bare}"
            else:
                byok_model = f"openai/{bare}"
                base_url = base_url.rstrip("/")
                if not base_url.endswith("/v1"):
                    base_url = f"{base_url}/v1"
        cands.append(
            RoutingCandidate(
                kind=kind,
                model=byok_model,
                base_url=base_url,
                adapter=LiteLLMAdapter(kind, dk.api_key, base_url, extra=dk.extra),
                is_mock=False,
                decrypted=dk,
            )
        )

    # 2) Server-side fallback env keys (stable order; skip the BYOK kind).
    for kind in _ENV_ORDER:
        if kind == byok_kind:
            continue
        key = _env_key(settings, kind)
        if key:
            cands.append(
                _env_candidate(
                    kind=kind,
                    key=key,
                    settings=settings,
                )
            )

    # 3) Ollama last-resort local node (no key, base_url only). Only when the
    #    user has configured an endpoint — zero-config compose has no Ollama.
    if settings.ollama_base_url and byok_kind != "ollama":
        cands.append(
            RoutingCandidate(
                kind="ollama",
                model=DEFAULT_MODELS["ollama"],
                base_url=settings.ollama_base_url,
                adapter=LiteLLMAdapter("ollama", "", settings.ollama_base_url),
                is_mock=False,
                decrypted=None,
            )
        )

    # 4) Mock stand-in — always last, so a turn always completes.
    cands.append(
        RoutingCandidate(
            kind="mock",
            model=DEFAULT_MODELS["mock"],
            base_url=None,
            adapter=MockAdapter(),
            is_mock=True,
            decrypted=None,
        )
    )
    return cands


def resolve_provider(
    *,
    enc_key_blob: str | None,
    settings: Settings,
    ecdh: SessionECDH,
    model: str | None = None,
    byok_decrypted: DecryptedKey | None = None,
) -> ResolvedProvider:
    """Phase 2 single-shot view: the first candidate of the chain."""
    cands = build_chain(
        enc_key_blob=enc_key_blob,
        settings=settings,
        ecdh=ecdh,
        model=model,
        byok_decrypted=byok_decrypted,
    )
    first = cands[0]
    return ResolvedProvider(
        adapter=first.adapter,
        model=first.model,
        kind=first.kind,
        is_mock=first.is_mock,
        decrypted=first.decrypted,
    )


__all__ = [
    "DEFAULT_MODELS",
    "UTILITY_MODELS",
    "utility_model_for",
    "ProviderResolutionError",
    "ResolvedProvider",
    "RoutingCandidate",
    "build_chain",
    "configured_env_kinds",
    "resolve_provider",
]
