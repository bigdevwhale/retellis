"""Lightweight, deterministic crisis screening for the AI companion.

This is the ``safety/`` package's only module (K8). It is deliberately
zero-config — no LLM call, no API, no model dependency — matching the
codebase's philosophy for the other heuristic layers (the signed
feature-hashing embedder and the salience heuristic in ``memory/``).
A real LLM-judge guardrail is a post-MVP upgrade; this keyword-based screen
is the deterministic floor that guarantees a crisis resource is surfaced
even when no provider key is configured (env/mock path) and even before
the first token is generated.

Scope:
- ``screen_user_message`` runs on ``body.message`` BEFORE the provider chain
  is built. A crisis flag short-circuits the turn: the companion replies
  with a compassionate, localized crisis-resource message instead of
  forwarding the content to the LLM. The user still gets a reply bubble and
  a persisted assistant event; the wire contract (session → token → done)
  is unchanged (the resource is emitted as ordinary tokens).
- ``screen_assistant_text`` runs on the streamed ``assistant_text`` AFTER the
  stream completes and BEFORE ``done``. If the model produced self-harm
  language without surfacing a resource, append the resource line as one
  final token (and to ``assistant_text``) so it reaches the user and is
  persisted. This is defense-in-depth; the persona block already *instructs*
  the model to direct to emergency services, but instructions are not
  enforcement.

Honest limits (disclose, don't perform):
- This is a high-precision keyword/phrase screen, not a classifier. It will
  miss paraphrased crisis language (low recall) and is not a substitute for
  a trained clinician or a hosted crisis-line integration. It guarantees a
  resource is surfaced for the common explicit cases; it does NOT claim to
  detect all risk.
- The companion does not claim feelings it doesn't have and does not claim
  confidentiality it can't guarantee (per the project's "disclose, don't
  perform" invariant). The resource message is a concrete action, not an
  affective claim.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyScreen:
    level: str  # "ok" | "crisis"
    # Localized crisis-resource paragraph. ``None`` when level == "ok".
    message_en: str | None = None
    message_ru: str | None = None

    def localized_message(self, source_text: str) -> str | None:
        """Pick the resource paragraph matching the language of the text that
        triggered the screen: Cyrillic present → Russian, else English. A user
        who wrote «хочу умереть» must get the resource in Russian, not an
        English template."""
        if self.message_ru and _has_cyrillic(source_text):
            return self.message_ru
        return self.message_en


# High-precision crisis phrases. Lowercased; matched as substrings against the
# lowercased input. Intentionally narrow to avoid false-positives on ordinary
# emotional conversation ("I'm so stressed", "hard week"). These are the
# explicit self-harm / suicidal-intent signals where surfacing a resource is
# clearly the right action.
_CRISIS_PHRASES_EN: tuple[str, ...] = (
    "kill myself",
    "killing myself",
    "want to die",
    "wants to die",
    "end my life",
    "ending my life",
    "suicidal",
    "suicide",
    "hurt myself",
    "hurting myself",
    "self-harm",
    "self harm",
    "cut myself",
    "cutting myself",
    "no reason to live",
    "better off dead",
    "take my own life",
)

_CRISIS_PHRASES_RU: tuple[str, ...] = (
    "покончить с собой",
    "покончить жизнь самоубийством",
    "хочу умереть",
    "хочу покончить",
    "суицид",
    "суицидальн",
    "самоубийств",
    "причинить себе вред",
    "навредить себе",
    "режу себя",
    "порезать себя",
    "нет смысла жить",
    "лучше бы я умер",
    "сведу счеты с жизнью",
    "свести счеты с жизнью",
)

# A resource line already present in the text means we don't need to append
# one again (avoids duplicate resource paragraphs on the output screen).
_RESOURCE_MARKERS: tuple[str, ...] = (
    "988",  # US Suicide & Crisis Lifeline
    "112",  # EU/general emergency
    "crisis line",
    "crisis hotline",
    "suicide prevention",
    "emergency services",
    "кризисная линия",
    "кризисная помощь",
    "службу спасения",
    "экстренная помощь",
    "телефон доверия",
)

_RESOURCE_EN = (
    "I'm really sorry you're feeling this way, and I want you to know you're "
    "not alone. If you're thinking about harming yourself, please reach out "
    "to someone who can help right now — in the US you can call or text 988 "
    "(Suicide & Crisis Lifeline, 24/7); in Europe call 112 for emergency "
    "services; or go to your nearest emergency room. You can also talk to a "
    "trusted person or a mental-health professional. This companion is here "
    "to listen, but it isn't a substitute for crisis care."
)

_RESOURCE_RU = (
    "Мне очень жаль, что вам сейчас так тяжело, и я хочу, чтобы вы знали: "
    "вы не одни. Если у вас появляются мысли о том, чтобы причинить себе "
    "вред, пожалуйста, обратитесь за помощью прямо сейчас — в США звоните "
    "или пишите на номер 988 (линия помощи в кризисе, круглосуточно); в "
    "Европе звоните 112 для экстренных служб; или обратитесь в ближайшее "
    "отделение неотложной помощи. Вы также можете поговорить с человеком, "
    "которому доверяете, или с психологом. Этот спутник готов выслушать, но "
    "он не заменяет профессиональную кризисную помощь."
)


def _lower(text: str) -> str:
    # The Russian phrases use Cyrillic; Python's str.lower handles Unicode
    # case folding for both scripts correctly here.
    return text.lower()


def _has_cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def _contains_crisis(text_lower: str) -> bool:
    return any(p in text_lower for p in _CRISIS_PHRASES_EN) or any(
        p in text_lower for p in _CRISIS_PHRASES_RU
    )


def _has_resource(text_lower: str) -> bool:
    return any(p in text_lower for p in _RESOURCE_MARKERS)


def screen_user_message(message: str) -> SafetyScreen:
    """Screen the user's inbound message before the provider chain runs.

    Returns a ``SafetyScreen`` with ``level="crisis"`` and localized resource
    paragraphs when explicit self-harm / suicidal-intent language is present.
    The router uses this to short-circuit the turn with the resource instead
    of forwarding the content to the LLM.
    """
    if not message:
        return SafetyScreen(level="ok")
    t = _lower(message)
    if _contains_crisis(t):
        return SafetyScreen(level="crisis", message_en=_RESOURCE_EN, message_ru=_RESOURCE_RU)
    return SafetyScreen(level="ok")


def screen_assistant_text(assistant_text: str, *, lang: str = "en") -> SafetyScreen:
    """Screen the streamed assistant reply after it completes, before ``done``.

    If the model produced crisis language WITHOUT already surfacing a
    resource, return the resource so the router can append it as one final
    token (and to ``assistant_text`` for persistence). Defense-in-depth: the
    persona block instructs the model to direct to emergency services, but
    instructions are not enforcement. When a resource is already present, or
    the reply has no crisis language, returns ``level="ok"``.
    """
    if not assistant_text:
        return SafetyScreen(level="ok")
    t = _lower(assistant_text)
    if _contains_crisis(t) and not _has_resource(t):
        return SafetyScreen(level="crisis", message_en=_RESOURCE_EN, message_ru=_RESOURCE_RU)
    return SafetyScreen(level="ok")
