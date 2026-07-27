"""Emit the pydantic-side JSON-Schema to ``schema.json`` (next to this script).

The drift check reads this and compares it against the zod-generated schema.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running without `pip install -e .` — put the local src on the path so
# `docker compose up` and CI work zero-config.
_SRC = Path(__file__).resolve().parent.parent / "src" / "py"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from ai_companion_contracts import (
        AuthConfig,
        BillingWebhookAck,
        CheckoutRequest,
        CheckoutSession,
        ConversationSummary,
        DoneEvent,
        ErrorEvent,
        Event,
        EventChain,
        FallbackEvent,
        Family,
        FamilyInvite,
        FamilyMember,
        FamilyProvider,
        FamilyTherapistPrompt,
        FamilyTherapistPromptSet,
        FeatureFlags,
        JournalEntry,
        JournalTagListResponse,
        LlmStreamRequest,
        LocalLoginRequest,
        LocalSignupRequest,
        MagicLinkRequest,
        Memory,
        MemoryShare,
        Messenger,
        MessengerPatchRequest,
        Persona,
        Plan,
        PortalSession,
        Principal,
        Provider,
        ProviderSummary,
        RoutingNode,
        RoutingState,
        SessionEvent,
        SessionInfo,
        Subscription,
        TelegramBindRequest,
        TelegramInitRequest,
        TelegramInitResponse,
        TokenEvent,
        Tone,
        Usage,
        UsageEvent,
    )
except ModuleNotFoundError as exc:  # pydantic / contracts not installed in this env
    print(
        f"[contracts] skipping schema regen ({exc}). "
        "Install pydantic + the contracts package (or run in the venv / docker) to regen; "
        "falling back to the committed schema.json for the drift check.",
        flush=True,
    )
    raise SystemExit(0) from exc

REGISTRY = {
    "Tone": Tone,
    "Provider": Provider,
    "Persona": Persona,
    "Event": Event,
    "EventChain": EventChain,
    "ConversationSummary": ConversationSummary,
    "Memory": Memory,
    "MemoryShare": MemoryShare,
    "JournalEntry": JournalEntry,
    "JournalTagListResponse": JournalTagListResponse,
    "Usage": Usage,
    "RoutingNode": RoutingNode,
    "ProviderSummary": ProviderSummary,
    "RoutingState": RoutingState,
    "LlmStreamRequest": LlmStreamRequest,
    "SessionEvent": SessionEvent,
    "TokenEvent": TokenEvent,
    "FallbackEvent": FallbackEvent,
    "UsageEvent": UsageEvent,
    "DoneEvent": DoneEvent,
    "ErrorEvent": ErrorEvent,
    "Principal": Principal,
    "Family": Family,
    "FamilyMember": FamilyMember,
    "FamilyInvite": FamilyInvite,
    "FamilyProvider": FamilyProvider,
    "FamilyTherapistPrompt": FamilyTherapistPrompt,
    "FamilyTherapistPromptSet": FamilyTherapistPromptSet,
    "FeatureFlags": FeatureFlags,
    "AuthConfig": AuthConfig,
    "LocalSignupRequest": LocalSignupRequest,
    "LocalLoginRequest": LocalLoginRequest,
    "MagicLinkRequest": MagicLinkRequest,
    "SessionInfo": SessionInfo,
    "Plan": Plan,
    "Subscription": Subscription,
    "CheckoutRequest": CheckoutRequest,
    "CheckoutSession": CheckoutSession,
    "PortalSession": PortalSession,
    "BillingWebhookAck": BillingWebhookAck,
    "Messenger": Messenger,
    "TelegramInitRequest": TelegramInitRequest,
    "TelegramInitResponse": TelegramInitResponse,
    "TelegramBindRequest": TelegramBindRequest,
    "MessengerPatchRequest": MessengerPatchRequest,
}


def main() -> None:
    defs: dict[str, dict] = {}
    for name, model in REGISTRY.items():
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        defs[name] = schema

    out = {"$defs": defs}
    target = Path(__file__).resolve().parent.parent / "schema.json"
    target.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {target} ({len(defs)} models)")


if __name__ == "__main__":
    main()