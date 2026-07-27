"""Safety package — deterministic crisis screening (K8).

A zero-config, keyword-based floor that guarantees a crisis resource is
surfaced for explicit self-harm / suicidal-intent language, before the
provider chain runs (inbound) and after the assistant reply completes
(outbound, defense-in-depth). An LLM-judge guardrail is a post-MVP upgrade
that would slot in alongside this screen without changing its signature.
"""

from .screen import SafetyScreen, screen_assistant_text, screen_user_message

__all__ = ["SafetyScreen", "screen_assistant_text", "screen_user_message"]
