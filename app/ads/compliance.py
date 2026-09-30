"""Advertising rules enforced in code before anything is published or launched.

Covers what the platforms and the law care about for these product lines:
no pharmaceutical advertising at all, no misleading condition claims for used
goods, no implied manufacturer endorsement, no guarantees or superlatives,
platform length and style limits, and the same fact rules as e-mail (every
figure must come from the catalogue or price book).
"""

from __future__ import annotations

import re
from typing import Any

from app.core.types import ProductCategory
from app.policies.fact_check import validate_message

# Pharmaceuticals are never advertised: Google and Meta restrict prescription
# drug ads to certified advertisers in a few countries, and a wrong ad is a
# regulatory problem, not a marketing one.
ADS_FORBIDDEN_CATEGORIES = {ProductCategory.PHARMA.value}

BANNED_AD_PHRASES = [
    "guarantee", "guaranteed", "100%", "risk-free", "risk free", "best price", "lowest price",
    "cheapest", "no.1", "number one", "#1", "unbeatable", "miracle", "act now", "limited time only",
    "free shipping",  # a commercial promise NEXUS cannot verify
]

CATEGORY_BANNED = {
    # Used and refurbished goods must never read as new.
    ProductCategory.IPHONE.value: ["brand new", "factory sealed", "apple certified", "apple authorized",
                                   "apple authorised", "official apple", "genuine apple warranty"],
    ProductCategory.LAPTOP.value: ["brand new", "factory sealed", "dell certified", "hp certified",
                                   "lenovo certified", "manufacturer warranty"],
    ProductCategory.SERVER_IT.value: ["brand new", "factory sealed", "manufacturer warranty"],
    ProductCategory.MEDICAL.value: ["fda approved", "fda-approved", "ce marked", "cures", "treats",
                                    "diagnoses", "clinically proven", "safe for all"],
}

GOOGLE_LIMITS = {"headline": 30, "description": 90, "headlines_min": 3, "headlines_max": 15,
                 "descriptions_min": 2, "descriptions_max": 4, "path": 15}
META_LIMITS = {"primary_text": 500, "headline": 40, "description": 30}
META_CTAS = {"GET_QUOTE", "CONTACT_US", "LEARN_MORE", "SHOP_NOW", "SEND_MESSAGE", "WHATSAPP_MESSAGE"}


def ad_allowed(category: str) -> bool:
    return category not in ADS_FORBIDDEN_CATEGORIES


def claim_issues(text: str, category: str) -> list[str]:
    lowered = text.lower()
    issues = [f"banned_claim:{p}" for p in BANNED_AD_PHRASES if p in lowered]
    issues += [f"banned_claim:{p}" for p in CATEGORY_BANNED.get(category, []) if p in lowered]
    return issues


def style_issues(text: str) -> list[str]:
    """Editorial rules both platforms enforce (and that make ads look spammy)."""
    issues = []
    if "!!" in text or "??" in text:
        issues.append("style:repeated punctuation")
    if text.count("!") > 1:
        issues.append("style:more than one exclamation mark")
    for word in re.findall(r"\b[A-Z]{4,}\b", text):
        if word not in {"USD", "EUR", "GBP", "KES", "IPHONE", "HPE", "RAM", "SSD", "NGO", "NGOS", "ICT", "UNGM", "CIF", "DAP", "FOB", "EXW", "MOQ"}:
            issues.append(f"style:all caps '{word}'")
            break
    return issues


def _facts_check(texts: list[str], facts: dict[str, Any], numbers: list[Any]) -> list[str]:
    result = validate_message(texts[0] if texts else "", "\n".join(texts), {
        "numbers": numbers,
        "license_verified": bool(facts.get("licence_statement")),
        "regulatory_verified": False,
        "relationship_verified": False,
    })
    return list(result.unsupported)


def check_google(content: dict[str, Any], category: str, facts: dict[str, Any], numbers: list[Any]) -> list[str]:
    heads = [h for h in content.get("headlines") or [] if h]
    descs = [d for d in content.get("descriptions") or [] if d]
    issues = []
    if not GOOGLE_LIMITS["headlines_min"] <= len(heads) <= GOOGLE_LIMITS["headlines_max"]:
        issues.append(f"google: need 3-15 headlines, got {len(heads)}")
    if not GOOGLE_LIMITS["descriptions_min"] <= len(descs) <= GOOGLE_LIMITS["descriptions_max"]:
        issues.append(f"google: need 2-4 descriptions, got {len(descs)}")
    issues += [f"google: headline over 30 characters: '{h}'" for h in heads if len(h) > GOOGLE_LIMITS["headline"]]
    issues += [f"google: description over 90 characters: '{d[:40]}...'" for d in descs if len(d) > GOOGLE_LIMITS["description"]]
    if len({h.lower() for h in heads}) != len(heads):
        issues.append("google: duplicate headlines")
    for text in heads + descs:
        issues += style_issues(text)
    issues += claim_issues("\n".join(heads + descs), category)
    issues += _facts_check(heads + descs, facts, numbers)
    return issues


def check_meta(content: dict[str, Any], category: str, facts: dict[str, Any], numbers: list[Any]) -> list[str]:
    issues = []
    for field, limit in META_LIMITS.items():
        value = content.get(field) or ""
        if field != "description" and not value:
            issues.append(f"meta: missing {field}")
        if len(value) > limit:
            issues.append(f"meta: {field} over {limit} characters")
    if content.get("call_to_action") not in META_CTAS:
        issues.append("meta: call to action must be one of " + ", ".join(sorted(META_CTAS)))
    texts = [content.get("headline") or "", content.get("primary_text") or "", content.get("description") or ""]
    for text in texts:
        issues += style_issues(text)
    issues += claim_issues("\n".join(texts), category)
    issues += _facts_check([t for t in texts if t], facts, numbers)
    return issues


def check_keywords(keywords: list[str]) -> tuple[list[str], list[str]]:
    """(accepted, rejected) keywords: plain words, 1-6 words, no competitor names of banned sort."""
    accepted, rejected = [], []
    for kw in keywords:
        kw = " ".join(str(kw).lower().split())
        if not kw or len(kw) > 80 or len(kw.split()) > 6 or re.search(r"[^a-z0-9 +\-.'&]", kw):
            rejected.append(kw)
            continue
        if any(p in kw for p in ("free", "crack", "torrent", "job", "jobs", "salary", "repair", "driver download")):
            rejected.append(kw)
            continue
        if kw not in accepted:
            accepted.append(kw)
    return accepted, rejected
