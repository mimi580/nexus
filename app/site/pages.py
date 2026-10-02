"""Landing pages: one per product line and market, written from facts NEXUS holds.

Every figure and claim on a page comes from your catalogue (products,
conditions, warranty, lead times), your price book ("from" prices), your
licence register and your business identity. The AI writes the words; the
fact check and the ad-claim rules decide whether they may be published. If
the AI's copy fails the checks, a plain template built from the same facts
is published instead — a page never goes out with an unsupported claim.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.agents.translate import back_translate, translate_fields
from app.core import languages
from app.commercial import catalogue
from app.core.context import RunContext
from app.core.interfaces import AgentResult
from app.core.types import ModelTier, ProductCategory, REGULATED_CATEGORIES
from app.database.models import LandingPage
from app.policies.fact_check import NUMBER_RE, validate_message
from app.policies.licenses import canonical_country, coverage

CATEGORY_TITLES = {
    ProductCategory.LAPTOP.value: "Refurbished business laptops",
    ProductCategory.IPHONE.value: "Used and refurbished iPhones",
    ProductCategory.MEDICAL.value: "Medical and hospital equipment",
    ProductCategory.PHARMA.value: "Pharmaceutical supplies",
    ProductCategory.SERVER_IT.value: "Enterprise servers and IT equipment",
}

CATEGORY_BUYERS = {
    ProductCategory.LAPTOP.value: "schools, universities, NGOs and businesses",
    ProductCategory.IPHONE.value: "phone retailers, wholesalers and corporate buyers",
    ProductCategory.MEDICAL.value: "hospitals, clinics and diagnostic centres",
    ProductCategory.PHARMA.value: "licensed pharmacies, distributors and hospitals",
    ProductCategory.SERVER_IT.value: "banks, data centres, ISPs and enterprises",
}


def slug_for(category: str, country: str, language: str = "en") -> str:
    title = CATEGORY_TITLES.get(category, category).lower()
    text = f"{title} {canonical_country(country)}"
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:110]
    return slug if language == "en" else f"{slug}-{language}"


def page_facts(ctx: RunContext, category: str, country: str, language: str = "en") -> dict[str, Any]:
    """Everything a page may state, gathered from records NEXUS holds."""
    today = ctx.now.date()
    country = canonical_country(country)
    offers = catalogue.current_offers(ctx.session, category, today)
    seen, products = set(), []
    for offer in sorted(offers, key=lambda o: o.get("unit_cost_usd") or 0):
        name = offer.get("product_name")
        if not name or name in seen:
            continue
        seen.add(name)
        products.append({
            "name": name,
            "condition": offer.get("condition"),
            "warranty": None,
            "lead_time_days": offer.get("lead_time_days"),
            "moq": offer.get("moq"),
        })
        if len(products) >= 6:
            break
    # Warranty is per offer; fetch it from the offer rows.
    from app.database.models import SupplierOffer

    for product in products:
        row = ctx.session.scalar(select(SupplierOffer).where(SupplierOffer.product_name == product["name"],
                                                            SupplierOffer.active.is_(True)))
        if row is not None and row.warranty:
            product["warranty"] = row.warranty
    price = catalogue.price_reference(ctx.session, category, country, today)
    licence = None
    if category in {c.value for c in REGULATED_CATEGORIES}:
        cover = coverage(ctx.session, country, category, today)
        if cover.covered and cover.license is not None:
            lic = cover.license
            licence = f"{lic.holder_name} is a licensed {' and '.join(lic.license_types)} in {lic.country}"
    s = ctx.settings
    return {
        "category": category,
        "language": language,
        "category_title": CATEGORY_TITLES.get(category, category),
        "buyers": CATEGORY_BUYERS.get(category, "organisations"),
        "country": country,
        "products": products,
        "from_price_usd": round(price.unit_price_low_usd, 2) if price else None,
        "price_basis": price.basis if price else None,
        "licence_statement": licence,
        "business_name": s.business_name or "NEXUS Sourcing",
        "business_address": s.business_postal_address,
        "whatsapp": bool(s.whatsapp_number),
        "response_promise": s.lead_response_promise,
    }


def allowed_numbers(facts: dict[str, Any]) -> list[str]:
    import json

    return [m.group(1).replace(",", "") for m in NUMBER_RE.finditer(json.dumps(facts, default=str))]


def content_text(content: dict[str, Any]) -> str:
    parts = [content.get("headline", ""), content.get("subheadline", ""), content.get("about", ""),
             content.get("cta", ""), content.get("licence_statement", "")]
    parts += content.get("benefits") or []
    for item in content.get("faq") or []:
        parts += [item.get("q", ""), item.get("a", "")]
    return "\n".join(str(p) for p in parts if p)


def check_content(content: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """Problems that stop publication (fact check + advertising claim rules)."""
    from app.ads.compliance import claim_issues

    text = content_text(content)
    language = facts.get("language") or "en"
    result = validate_message(content.get("headline", ""), text, {
        "language": language,
        "numbers": allowed_numbers(facts),
        "license_verified": bool(facts.get("licence_statement")),
        "regulatory_verified": False,
        "relationship_verified": False,
    })
    issues = list(result.unsupported)
    issues += claim_issues(text, facts["category"], language)
    if not content.get("headline"):
        issues.append("missing headline")
    return issues


def template_content(facts: dict[str, Any]) -> dict[str, Any]:
    """Plain copy from facts alone: the safe fallback."""
    lines = [
        f"Quotations for {facts['buyers']} in {facts['country']}",
        f"Tell us what you need and we reply {facts['response_promise']}",
    ]
    if facts.get("products"):
        lines.append("Specific models, grades and warranty terms listed below")
    if facts.get("licence_statement"):
        lines.append(facts["licence_statement"])
    return {
        "headline": f"{facts['category_title']} for {facts['country']}",
        "subheadline": f"Supply for {facts['buyers']}. Request a quotation for your quantity.",
        "benefits": lines,
        "about": f"{facts['business_name']} sources and supplies {facts['category_title'].lower()} for organisations in {facts['country']}.",
        "faq": [
            {"q": "How do I get a price?", "a": f"Send the form with your quantity. We reply {facts['response_promise']}."},
            {"q": "Can I ask about delivery?", "a": f"Yes. Tell us your location in {facts['country']} and we confirm delivery terms in the quotation."},
        ],
        "cta": "Request a quotation",
        **({"licence_statement": facts["licence_statement"]} if facts.get("licence_statement") else {}),
    }


CONTENT_KEYS = ("headline", "subheadline", "benefits", "about", "faq", "cta", "licence_statement")


class LandingPageAgent(BaseAgent):
    name = "landing_page"
    task_type = "landing_copy"
    tier = ModelTier.REASONING
    complexity = 0.6

    def _verify(self, ctx: RunContext, content: dict[str, Any], facts: dict[str, Any]) -> tuple[list[str], float]:
        """Checks on the text itself and, for other languages, on an independent English back-translation."""
        issues = check_content(content, facts)
        language = facts.get("language") or "en"
        if issues or language == "en":
            return issues, 0.0
        english, cost = back_translate(self, ctx, {k: v for k, v in content.items() if v}, language)
        if not english.get("headline"):
            return ["the translation could not be verified in English"], cost
        return check_content(english, {**facts, "language": "en"}), cost

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        category = task_input["product_category"]
        country = canonical_country(task_input["country"])
        language = task_input.get("language") or languages.language_for(ctx.session, country)
        if not languages.supported(language):
            return self.fail(f"unsupported language {language!r}")
        facts = page_facts(ctx, category, country, language)
        prompt = (
            "Write conversion-focused landing page copy for B2B buyers using ONLY these facts. Lead with "
            "the buyer's need, be specific (models, grades, warranty, lead time) when facts give them, and "
            "make requesting a quotation the single clear action. No superlatives, no guarantees, no claims "
            "about certifications, approvals, stock levels or delivery times that are not in the facts. "
            "Headline under 70 characters. Return {'headline','subheadline','benefits':[3-5 short lines],"
            "'about','faq':[{'q','a'}],'cta'}."
        )
        if language != "en":
            prompt += (
                f" Write everything in {languages.language_name(language)} as a local business would; write the "
                f"country as '{languages.country_name(language, country)}'; keep product and brand names and 'USD' "
                "in Latin letters and use Western digits (0-9). If the facts include a licence_statement, also "
                "return 'licence_statement': a faithful translation of it, nothing added."
            )
        data, cost = self.ask(ctx, prompt, facts)
        content = {k: data.get(k) for k in CONTENT_KEYS if data.get(k)}
        issues, extra = self._verify(ctx, content, facts)
        cost += extra
        used_template = False
        if issues:
            content, used_template = template_content(facts), True
            if language != "en":  # the plain English version, translated and verified the same way
                content, extra = translate_fields(self, ctx, content, language)
                cost += extra
            issues_after, extra = self._verify(ctx, content, facts)
            cost += extra
            if issues_after:
                return self.fail(f"page could not be made compliant: {issues_after}", cost_usd=cost)

        slug = slug_for(category, country, language)
        page = ctx.session.scalar(select(LandingPage).where(LandingPage.slug == slug))
        if page is None:
            page = LandingPage(slug=slug, product_category=category, country=country, version=1)
            ctx.session.add(page)
        else:
            page.version += 1
        page.language = language
        page.content = content
        page.facts = facts
        page.status = "published"
        ctx.session.flush()
        ctx.audit.record(
            "landing_page_published", summary=f"{slug} v{page.version}" + (" (template: AI copy failed checks)" if used_template else ""),
            decision="allow", task_id=ctx.task_id, issues=issues,
        )
        output = {"slug": slug, "version": page.version, "language": language, "template": used_template,
                  "rejected_copy_issues": issues}
        # Buyers who prefer English get an English version of the same page, linked from this one.
        if language != "en" and ensure_page(ctx, category, country, "en") is None:
            english = self.run(ctx, {"product_category": category, "country": country, "language": "en"})
            cost += english.cost_usd
            output["english_slug"] = english.output.get("slug") if english.ok else None
        return self.ok(output=output, cost_usd=cost)


def ensure_page(ctx: RunContext, category: str, country: str, language: str | None = None) -> LandingPage | None:
    language = language or languages.language_for(ctx.session, country)
    page = ctx.session.scalar(select(LandingPage).where(LandingPage.slug == slug_for(category, country, language)))
    return page if page is not None and page.status == "published" else None


def page_url(settings: Any, page: LandingPage) -> str | None:
    return f"{settings.site_url}/p/{page.slug}" if settings.site_url else None
