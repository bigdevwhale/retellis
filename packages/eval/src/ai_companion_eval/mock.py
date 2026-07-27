"""Local mock turn — mirrors ``ai_companion_api.llm.mock_adapter.MockAdapter``.

Replicated here (instead of imported) so the eval package stays self-contained:
no pynacl / litellm / fastapi needed to run ``pnpm eval``. The mock reply is
honest about being a stand-in (disclose, don't perform), echoes the user's last
message, and asks one reflective question. Keep this in sync with the API's
``MockAdapter.stream`` reply string.
"""

from __future__ import annotations


def mock_reply(messages: list[dict[str, str]]) -> str:
    last_user = ""
    for m in messages:
        if m.get("role") == "user":
            last_user = m.get("content", "")
    snippet = last_user.strip().replace("\n", " ")
    if len(snippet) > 140:
        snippet = snippet[:137] + "…"
    return (
        "(offline stand-in — no provider key connected) "
        f'I hear that: “{snippet}”. '
        "What feels like the next small step from here?"
    )


__all__ = ["mock_reply"]