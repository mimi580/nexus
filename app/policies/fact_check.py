"""Deterministic outbound fact validation.

The model writes copy; this module decides whether the copy is allowed to
leave the building. Anything asserted that is not in the evidence-backed fact
set is treated as fabricated.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

BANNED_MEDICAL_CLAIMS = [
    "cures",
    "guaranteed cure",
    "treats all",
    "no side effects",
    "clinically proven to cure",
    "fda approved by us",
    "safe for all patients",
]

REGULATORY_TOKENS = [
    "fda approved",
    "fda-approved",
    "ce marked",
    "ce certified",
    "who prequalified",
    "iso certified",
    "pharmacy and poisons board approved",
    "gmp certified",
]

RELATIONSHIP_TOKENS = [
    "authorized distributor",
    "authorised distributor",
    "exclusive partner",
    "official reseller",
    "factory authorized",
    "we supply",
]

LICENSE_TOKENS = [
    "licensed importer",
    "licensed distributor",
    "licensed wholesaler",
    "licensed pharmaceutical",
    "licensed medical",
    "import licence",
    "import license",
    "registered importer",
    "registered distributor",
]

NUMBER_RE = re.compile(r"(?<![\w.])(?:usd\s*)?\$?\s?(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


class FactCheckResult(BaseModel):
    ok: bool
    unsupported: list[str] = Field(default_factory=list)
    checked_numbers: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict:
        return self.model_dump()


def _normalize_number(token: str) -> str:
    cleaned = token.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return f"{value:g}"


def validate_message(
    subject: str,
    body: str,
    allowed_facts: dict | None = None,
) -> FactCheckResult:
    facts = allowed_facts or {}
    text = f"{subject}\n{body}"
    lowered = text.lower()
    unsupported: list[str] = []

    for phrase in BANNED_MEDICAL_CLAIMS:
        if phrase in lowered:
            unsupported.append(f"medical_claim:{phrase}")

    if not facts.get("regulatory_verified"):
        for token in REGULATORY_TOKENS:
            if token in lowered:
                unsupported.append(f"regulatory_claim:{token}")

    if not facts.get("relationship_verified"):
        for token in RELATIONSHIP_TOKENS:
            if token in lowered:
                unsupported.append(f"relationship_claim:{token}")

    if not facts.get("license_verified"):
        for token in LICENSE_TOKENS:
            if token in lowered:
                unsupported.append(f"license_claim:{token}")

    if facts.get("operator_approved"):
        return FactCheckResult(ok=not unsupported, unsupported=unsupported, checked_numbers=[])

    allowed_numbers = {_normalize_number(str(n)) for n in facts.get("numbers", [])}
    checked: list[str] = []
    for match in NUMBER_RE.finditer(text):
        raw = match.group(1)
        if YEAR_RE.match(raw.replace(",", "")):
            continue
        normalized = _normalize_number(raw)
        if float(normalized) < 10:  # step numbers, list counts, "2 weeks"
            continue
        checked.append(normalized)
        if normalized not in allowed_numbers:
            unsupported.append(f"unsupported_figure:{normalized}")

    return FactCheckResult(ok=not unsupported, unsupported=unsupported, checked_numbers=checked)
