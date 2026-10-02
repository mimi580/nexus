"""Ad angles, keywords and copy: built from facts, checked, then tested against each other.

An angle is the one reason-to-enquire an ad leads with (price, lead time,
warranty, local support, licensed supply, range). An angle is only offered
when the facts support it: no warranty on file, no warranty angle. Each
angle has template copy made purely from facts, which always passes the
checks; the AI's copy replaces it only when it passes the same checks.

Search ads use phrase-match, high-intent keywords (wholesale, bulk,
supplier, for schools...), plus a standing list of negatives that keeps
money away from job seekers, repair searches and consumers wanting one unit.
"""

from __future__ import annotations

import re
from typing import Any

from app.ads.compliance import GOOGLE_LIMITS, META_LIMITS, check_google, check_keywords, check_meta
from app.core import languages
from app.core.types import ProductCategory
from app.site.pages import allowed_numbers

C = ProductCategory

NOUN = {
    C.LAPTOP.value: "Refurbished Laptops",
    C.IPHONE.value: "Refurbished iPhones",
    C.MEDICAL.value: "Medical Equipment",
    C.SERVER_IT.value: "Refurbished Servers",
}
BUYER_SHORT = {
    C.LAPTOP.value: "Schools, NGOs and Offices",
    C.IPHONE.value: "Phone Retailers",
    C.MEDICAL.value: "Hospitals and Clinics",
    C.SERVER_IT.value: "Banks, ISPs and Data Centres",
}

KEYWORDS = {
    C.LAPTOP.value: [
        "refurbished laptops wholesale", "bulk refurbished laptops", "refurbished laptops in bulk",
        "wholesale laptops supplier", "refurbished laptop supplier", "refurbished laptops for schools",
        "refurbished business laptops", "ex corporate laptops", "refurbished dell latitude",
        "refurbished lenovo thinkpad", "refurbished hp elitebook", "laptops for ngos",
        "refurbished laptops {country}", "laptop suppliers in {country}", "wholesale laptops {country}",
    ],
    C.IPHONE.value: [
        "refurbished iphones wholesale", "used iphones wholesale", "bulk used iphones",
        "wholesale iphone supplier", "grade a used iphones", "refurbished iphone supplier",
        "used iphones in bulk", "iphone wholesale {country}", "refurbished iphones {country}",
        "used iphone suppliers {country}",
    ],
    C.MEDICAL.value: [
        "medical equipment supplier", "hospital equipment supplier", "patient monitor supplier",
        "ultrasound machine supplier", "ecg machine supplier", "laboratory analyser supplier",
        "refurbished medical equipment", "hospital equipment {country}", "medical equipment suppliers in {country}",
        "medical equipment distributor {country}",
    ],
    C.SERVER_IT.value: [
        "refurbished servers", "refurbished dell poweredge", "refurbished hpe proliant", "used servers supplier",
        "enterprise server supplier", "refurbished networking equipment", "server hardware supplier",
        "data centre equipment supplier", "refurbished servers {country}", "server suppliers in {country}",
    ],
}

NEGATIVES_COMMON = [
    "free", "jobs", "job", "vacancy", "vacancies", "salary", "career", "internship", "repair", "repairs",
    "fix", "driver", "drivers", "manual", "how to", "diy", "course", "training", "tutorial", "pdf",
    "rent", "rental", "hire", "review", "reviews", "specs", "wallpaper", "game", "games", "download",
]
NEGATIVES = {
    C.LAPTOP.value: ["screen replacement", "battery replacement", "charger", "keyboard replacement", "gaming laptop"],
    C.IPHONE.value: ["icloud", "unlock", "bypass", "jailbreak", "stolen", "screen replacement", "case", "cover",
                     "charger", "ringtone", "wallpaper"],
    C.MEDICAL.value: ["symptoms", "treatment", "disease", "nurse jobs", "doctor", "home use", "insurance"],
    C.SERVER_IT.value: ["minecraft", "discord", "vpn", "web hosting", "cloud server", "vps", "free server"],
}

ANGLES = ("price_value", "lead_time", "quality_warranty", "local_support", "compliance_docs", "range")


def _short(text: str, limit: int) -> str | None:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else None


def _title(text: str) -> str:
    return " ".join(w if (w.isupper() or any(ch.isdigit() for ch in w)) else w[:1].upper() + w[1:] for w in text.split())


def available_angles(facts: dict[str, Any]) -> list[str]:
    """Angles the facts can support."""
    products = facts.get("products") or []
    angles = []
    if facts.get("from_price_usd"):
        angles.append("price_value")
    if any(p.get("lead_time_days") for p in products):
        angles.append("lead_time")
    if any(p.get("warranty") for p in products) or any(p.get("condition") for p in products):
        angles.append("quality_warranty")
    if facts.get("whatsapp") or facts.get("response_promise"):
        angles.append("local_support")
    if facts.get("licence_statement"):
        angles.append("compliance_docs")
    if len(products) >= 2:
        angles.append("range")
    return angles


def _angle_facts(facts: dict[str, Any]) -> dict[str, Any]:
    products = facts.get("products") or []
    lead = min((int(p["lead_time_days"]) for p in products if p.get("lead_time_days")), default=None)
    warranty = next((p["warranty"] for p in products if p.get("warranty")), None)
    condition = next((p["condition"] for p in products if p.get("condition")), None)
    moq = min((int(p["moq"]) for p in products if p.get("moq")), default=None)
    return {"lead": lead, "warranty": warranty, "condition": condition, "moq": moq,
            "price": facts.get("from_price_usd"), "products": [p["name"] for p in products]}


def _price(value: float | None) -> str | None:
    if not value:
        return None
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def google_template(facts: dict[str, Any], angle: str) -> dict[str, Any]:
    """A responsive search ad (headlines ≤30, descriptions ≤90) led by one angle."""
    cat, country = facts["category"], facts["country"]
    noun, af = NOUN.get(cat, facts["category_title"]), _angle_facts(facts)
    price = _price(af["price"])
    lead_heads = {
        "price_value": [f"From USD {price} per Unit" if price else None, "Volume Pricing on Request",
                        f"{noun} From USD {price}" if price else None],
        "lead_time": [f"Lead Time About {af['lead']} Days" if af["lead"] else None, "Stock Confirmed in Writing",
                      "Quote Before You Commit"],
        "quality_warranty": [_title(f"{af['warranty']} Warranty") if af["warranty"] else None,
                             _title(af["condition"]) if af["condition"] else None, "Condition Graded in Writing"],
        "local_support": ["Order via WhatsApp" if facts.get("whatsapp") else None, f"Supplying {country}",
                          "Talk to a Real Person"],
        "compliance_docs": ["Licensed Distributor" if facts.get("licence_statement") else None,
                            "Documents With Your Quote", f"Supplying {country}"],
        "range": [*(_title(p) for p in af["products"][:3]), f"{len(af['products'])} Models Available"],
    }[angle]
    common = [f"{noun} {country}", f"Wholesale {noun}", f"Bulk {noun}", "Request a Quotation",
              f"For {BUYER_SHORT.get(cat, 'Organisations')}", "Reply Within One Business Day"
              if facts.get("response_promise") == "within one business day" else "Fast Written Quotations",
              f"MOQ {af['moq']} Units" if af["moq"] else None, f"{noun} Supplier"]
    headlines, seen = [], set()
    for h in lead_heads + common:
        h = _short(h, GOOGLE_LIMITS["headline"]) if h else None
        if h and h.lower() not in seen:
            seen.add(h.lower())
            headlines.append(h)
    lead_desc = {
        "price_value": f"{noun} from USD {price} per unit. Send your quantity for a written quotation." if price else None,
        "lead_time": f"Typical lead time about {af['lead']} days. Tell us your quantity and delivery point." if af["lead"] else None,
        "quality_warranty": (f"{af['condition'] or 'Condition'} units" + (f" with {af['warranty']} warranty" if af["warranty"] else "")
                             + ". Condition confirmed in writing."),
        "local_support": f"Supplying {facts['buyers']} in {country}. We reply {facts.get('response_promise') or 'quickly'}.",
        "compliance_docs": f"{facts.get('licence_statement') or ''}. Quotation with documents on request.",
        "range": f"Models include {', '.join(af['products'][:3])}. Request a quotation for your quantity.",
    }[angle]
    descriptions = []
    for d in [lead_desc, f"{facts['category_title']} for {facts['buyers']} in {country}. Request a quotation.",
              f"Tell us the quantity you need and we reply {facts.get('response_promise') or 'with a quotation'}.",
              f"{noun} for organisations in {country}. Request a written quotation.",
              "Send your quantity and delivery point for a written quotation."]:
        d = _short(d, GOOGLE_LIMITS["description"]) if d else None
        if d and d not in descriptions:
            descriptions.append(d)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", noun).strip("-")
    return {"headlines": headlines[:15], "descriptions": descriptions[:4], "paths": [slug[:15], country.replace(" ", "-")[:15]]}


def meta_template(facts: dict[str, Any], angle: str) -> dict[str, Any]:
    cat, country = facts["category"], facts["country"]
    noun, af = NOUN.get(cat, facts["category_title"]), _angle_facts(facts)
    price = _price(af["price"])
    opener = {
        "price_value": f"{noun} from USD {price} per unit for {facts['buyers']} in {country}.",
        "lead_time": f"Need {noun.lower()} in {country}? Typical lead time is about {af['lead']} days.",
        "quality_warranty": f"{af['condition'] or noun}" + (f", with {af['warranty']} warranty" if af["warranty"] else "") + ".",
        "local_support": f"Supplying {facts['buyers']} in {country}. Talk to us directly" + (" on WhatsApp" if facts.get("whatsapp") else "") + ".",
        "compliance_docs": f"{facts.get('licence_statement')}.",
        "range": f"{noun} for {country}: {', '.join(af['products'][:3])}.",
    }[angle]
    lines = [opener]
    if af["products"] and angle != "range":
        lines.append(f"Models: {', '.join(af['products'][:3])}.")
    if af["moq"]:
        lines.append(f"Minimum order {af['moq']} units.")
    lines.append(f"Tell us your quantity and we reply {facts.get('response_promise') or 'with a written quotation'}.")
    headline = next(h for h in [f"{noun} for {country}", f"{noun} Supplier", noun] if len(h) <= META_LIMITS["headline"])
    description = next((d for d in ["Get a written quotation", "Request a quotation"] if len(d) <= META_LIMITS["description"]), "")
    return {"primary_text": "\n".join(lines)[:META_LIMITS["primary_text"]], "headline": headline,
            "description": description, "call_to_action": "GET_QUOTE",
            "card": {"headline": headline, "subline": opener[:120], "cta": "Get a quotation"}}


def keyword_plan(facts: dict[str, Any], extra: list[str] | None = None,
                 language: str = "en") -> tuple[list[str], list[str], list[str]]:
    """(keywords, negatives, rejected suggestions), in the campaign's language.

    Arabic, Turkish and Hebrew campaigns use the local keyword bank when one
    exists for the product line; otherwise the English bank (many buyers in
    these markets also search in English).
    """
    cat, country = facts["category"], facts["country"].lower()
    local = languages.AD_KEYWORDS.get(language, {}).get(cat) if language != "en" else None
    if local:
        place = languages.country_name(language, facts["country"])
        base = [k.format(country=place) for k in local]
        local_negatives = languages.AD_NEGATIVES.get(language, [])
    else:
        base = [k.format(country=country) for k in KEYWORDS.get(cat, [])]
        local_negatives = []
    accepted, rejected = check_keywords(base + list(extra or []))
    negatives = NEGATIVES_COMMON + NEGATIVES.get(cat, []) + local_negatives
    accepted = [k for k in accepted if not any(re.search(rf"\b{re.escape(n)}\b", k) for n in negatives)]
    return accepted[:40], negatives, rejected


def check_variant(platform: str, content: dict[str, Any], facts: dict[str, Any], language: str = "en") -> list[str]:
    numbers = allowed_numbers(facts)
    if platform == "google":
        return check_google(content, facts["category"], facts, numbers, language)
    return check_meta(content, facts["category"], facts, numbers, language)


TRANSLATED_FIELDS = {"google": ("headlines", "descriptions"), "meta": ("primary_text", "headline", "description")}
TRANSLATION_LIMITS = {
    "google": {"each headline": GOOGLE_LIMITS["headline"], "each description": GOOGLE_LIMITS["description"]},
    "meta": {"primary_text": META_LIMITS["primary_text"], "headline": META_LIMITS["headline"],
             "description": META_LIMITS["description"]},
}


def template(platform: str, facts: dict[str, Any], angle: str) -> dict[str, Any]:
    return google_template(facts, angle) if platform == "google" else meta_template(facts, angle)
