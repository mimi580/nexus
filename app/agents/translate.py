"""Translation with a check: write in the buyer's language, verify in English.

NEXUS's safety rules (no invented figures, no unsupported claims) were built
on English text. For Arabic, Turkish and Hebrew the same rules are kept by
(1) checking the foreign text itself for figures and banned phrases, and
(2) having a separate model call translate it back into English literally,
then running the full English checks on that. You also see the English
version in Reviews, so you never approve text you cannot read.
"""

from __future__ import annotations

from typing import Any

from app.core.languages import ENGLISH, language_name
from app.core.types import ModelTier


def _fields(data: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
    out = data.get("fields") if isinstance(data.get("fields"), dict) else data
    if not isinstance(out, dict):
        return {}
    return {k: out[k] for k in original if out.get(k) not in (None, "", [])}


def back_translate(agent: Any, ctx: Any, fields: dict[str, Any], language: str) -> tuple[dict[str, Any], float]:
    """A literal English rendering of text NEXUS wrote in another language (independent, critic model)."""
    if language == ENGLISH or not fields:
        return dict(fields), 0.0
    data, cost = agent.ask(
        ctx,
        (
            f"Translate each field from {language_name(language)} into English, literally and completely. "
            "Do not improve, soften, summarise or omit anything: keep every number, name, claim and promise "
            "exactly as stated, even if it seems wrong. Return {'fields': {same keys}}."
        ),
        {"direction": "to_english", "language": language, "fields": fields},
        task_type="translation", tier=ModelTier.CRITIC, complexity=0.4,
    )
    return _fields(data, fields), cost


def translate_fields(agent: Any, ctx: Any, fields: dict[str, Any], language: str,
                     limits: dict[str, int] | None = None) -> tuple[dict[str, Any], float]:
    """Translate checked English text into the target language without adding anything."""
    if language == ENGLISH or not fields:
        return dict(fields), 0.0
    limit_note = ""
    if limits:
        limit_note = " Keep within these character limits per item: " + ", ".join(f"{k} {v}" for k, v in limits.items()) + "."
    data, cost = agent.ask(
        ctx,
        (
            f"Translate each field into {language_name(language)} as a native business writer would phrase it. "
            "Add nothing and drop nothing: no new claims, figures, guarantees or superlatives. Keep product and "
            "brand names, model numbers and 'USD' in Latin letters, and write all numbers with Western digits "
            f"(0-9). Lists stay lists with the same number of items.{limit_note} Return {{'fields': {{same keys}}}}."
        ),
        {"direction": "from_english", "language": language, "fields": fields},
        task_type="translation", tier=ModelTier.REASONING, complexity=0.5,
    )
    return _fields(data, fields), cost


def flatten(fields: dict[str, Any]) -> str:
    """All text in a (possibly nested) translation result, for checking."""
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(fields)
    return "\n".join(p for p in parts if p)


def final_text(agent: Any, ctx: Any, payload: dict[str, Any]) -> tuple[str, str, float]:
    """The subject and body to send for an approved draft.

    If you edited the English version of a foreign-language draft in Reviews,
    your English is translated into the buyer's language and that is what is sent.
    """
    subject, body = payload.get("subject", ""), payload.get("body", "")
    language = payload.get("language") or ENGLISH
    if language == ENGLISH or not payload.get("retranslate"):
        return subject, body, 0.0
    source = {"body": payload.get("body_english") or ""}
    if payload.get("subject_english"):
        source["subject"] = payload["subject_english"]
    translated, cost = translate_fields(agent, ctx, source, language)
    return translated.get("subject") or subject, translated.get("body") or body, cost
