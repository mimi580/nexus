"""Deterministic mock provider: the whole platform runs without credentials.

Given the same request it returns the same output, so simulations and tests are
reproducible. It draws from app.simulation.fixtures rather than inventing data
at random, and everything it produces is treated downstream as a model
hypothesis until evidence upgrades it.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from app.core.errors import ProviderUnavailable
from app.core.ids import stable_key
from app.core.interfaces import ModelRequest, ModelResponse
from app.core.types import ModelTier
from app.models.providers.base import BaseProvider, estimate_tokens, price
from app.simulation import fixtures


def _rng(request: ModelRequest) -> random.Random:
    seed = int(stable_key(request.task_type, request.prompt)[:8], 16)
    return random.Random(seed)


class MockProvider(BaseProvider):
    def __init__(
        self,
        name: str = "mock",
        failure_rate: float = 0.0,
        latency_ms: float = 4.0,
        malformed_rate: float = 0.0,
    ) -> None:
        self.name = name
        self.failure_rate = failure_rate
        self.latency_ms = latency_ms
        self.malformed_rate = malformed_rate
        self.calls = 0

    # ------------------------------------------------------------ generation
    def _market_research(self, ctx: dict, rng: random.Random) -> dict:
        category = ctx.get("product_category", "refurbished_laptop")
        markets = []
        for country, region, factors in fixtures.MARKETS:
            if "Europe" in region and category not in fixtures.EUROPE_ALLOWED_CATEGORIES:
                continue
            jitter = {k: round(min(1.0, max(0.0, v + rng.uniform(-0.07, 0.07))), 3) for k, v in factors.items()}
            markets.append(
                {
                    "country": country,
                    "region": region,
                    "factors": jitter,
                    "rationale": f"Observed procurement activity and supplier reach for {category} in {country}.",
                    "sources": [f"simulated-market-brief://{country.lower()}/{category}"],
                }
            )
        return {"markets": markets}

    def _prospects(self, ctx: dict, rng: random.Random) -> dict:
        category = ctx.get("product_category")
        countries = set(ctx.get("countries") or [])
        pool = [
            c
            for c in fixtures.COMPANIES
            if (not category or c["category"] == category)
            and (not countries or c["country"] in countries)
        ]
        limit = int(ctx.get("limit", 6))
        selected = pool[:limit]
        return {
            "prospects": [
                {
                    "name": c["name"],
                    "domain": c["domain"],
                    "country": c["country"],
                    "city": c["city"],
                    "segment": c["segment"],
                    "size_indicator": c["size_indicator"],
                    "description": f"{c['segment']} in {c['city']}, {c['country']}",
                    "buying_signals": c["signals"],
                    "source": f"simulated-directory://{c['domain']}",
                }
                for c in selected
            ]
        }

    def _company_intelligence(self, ctx: dict, rng: random.Random) -> dict:
        name = ctx.get("company_name", "the organisation")
        category = ctx.get("product_category", "")
        return {
            "needs": [f"periodic replacement of {category.replace('_', ' ')}"],
            "signals": ctx.get("buying_signals") or [f"public activity suggests {category} demand"],
            "size_indicator": ctx.get("size_indicator") or "unknown",
            "notes": f"{name} operates in {ctx.get('country', 'an unspecified country')}.",
            "confidence": round(rng.uniform(0.45, 0.8), 2),
            "sources": [f"simulated-web://{ctx.get('domain', 'unknown')}/about"],
        }

    def _decision_makers(self, ctx: dict, rng: random.Random) -> dict:
        category = ctx.get("product_category", "")
        domain = ctx.get("domain")
        roles = fixtures.CONTACT_ROLES.get(category, ["Procurement Manager"])
        contacts = []
        for role in roles[: int(ctx.get("limit", 1))]:
            first = rng.choice(fixtures.FIRST_NAMES)
            last = rng.choice(fixtures.LAST_NAMES)
            email = f"{first.lower()}.{last.lower()}@{domain}" if domain else None
            contacts.append(
                {
                    "full_name": f"{first} {last}",
                    "role": role,
                    "email": email,
                    "confidence": round(rng.uniform(0.4, 0.85), 2),
                    "source": f"simulated-directory://{domain}/team" if domain else "unknown",
                    "verified": False,
                }
            )
        return {"contacts": contacts}

    def _qualification(self, ctx: dict, rng: random.Random) -> dict:
        return {
            "product_fit": round(rng.uniform(0.4, 0.95), 2),
            "need_evidence": round(rng.uniform(0.3, 0.9), 2),
            "order_potential_units": int(ctx.get("typical_qty", 20) * rng.uniform(0.5, 1.6)),
            "timing": rng.choice(["this quarter", "next quarter", "unknown"]),
            "decision_maker_confidence": round(rng.uniform(0.3, 0.9), 2),
            "budget_indicator": rng.choice(["approved", "planned", "unknown"]),
            "supplier_feasibility": round(rng.uniform(0.4, 0.95), 2),
            "regulatory_risk": round(rng.uniform(0.05, 0.8), 2),
            "payment_risk": round(rng.uniform(0.1, 0.7), 2),
            "reasons": ["signals and segment align with the product line"],
        }

    def _sourcing(self, ctx: dict, rng: random.Random) -> dict:
        category = ctx.get("product_category")
        econ = fixtures.UNIT_ECONOMICS.get(category, {"unit_cost": (100.0, 150.0), "typical_qty": 10})
        offers = []
        for supplier in fixtures.SUPPLIERS:
            if category not in supplier["categories"]:
                continue
            low, high = econ["unit_cost"]
            unit = round(rng.uniform(low, high), 2)
            offers.append(
                {
                    "supplier_name": supplier["name"],
                    "country": supplier["country"],
                    "unit_cost_usd": unit,
                    "unit_cost_high_usd": round(unit * rng.uniform(1.02, 1.12), 2),
                    "quantity_available": int(econ.get("typical_qty", 20) * rng.uniform(1.0, 4.0)),
                    "condition": rng.choice(["grade A refurbished", "grade B refurbished", "new surplus"]),
                    "lead_time_days": supplier["lead_time_days"],
                    "shipping_cost_usd": round(rng.uniform(300, 1400), 2),
                    "payment_terms": supplier["payment_terms"],
                    "documents": supplier["documents"],
                    "reliability": supplier["reliability"],
                    "source": f"simulated-supplier-quote://{supplier['name'].lower().replace(' ', '-')}",
                }
            )
        return {"offers": offers}

    def _strategy(self, ctx: dict, rng: random.Random) -> dict:
        category = (ctx.get("product_category") or "").replace("_", " ")
        return {
            "segment": ctx.get("segment", "unknown"),
            "value_proposition": f"reliable supply of {category} with documented condition grading and short lead times",
            "channel": "email",
            "message_angle": rng.choice(
                ["cost per unit versus new", "lead time and availability", "documented condition and warranty"]
            ),
            "timing": "business hours, mid-week",
            "followup_cadence_days": rng.choice([3, 4, 5]),
        }

    def _outreach(self, ctx: dict, rng: random.Random) -> dict:
        company = ctx.get("company_name", "your organisation")
        contact = ctx.get("contact_name") or "there"
        category = (ctx.get("product_category") or "equipment").replace("_", " ")
        signal = (ctx.get("buying_signals") or ["your current procurement cycle"])[0]
        angle = ctx.get("message_angle", "availability and lead time")
        sender = ctx.get("sender_name", "NEXUS Sourcing")
        qty = ctx.get("quantity")
        qty_line = f"We can quote for quantities around {qty} units." if qty else "We can quote against your required quantity."
        license_line = f"{ctx['license_statement']}. " if ctx.get("license_statement") else ""
        subject = f"{category.title()} supply for {company}"
        body = (
            f"Hello {contact},\n\n"
            f"I saw {signal} at {company} and thought a quick note was worth your time.\n\n"
            f"{license_line}We source {category} and can work to your specification. Our focus is {angle}. "
            f"{qty_line} Condition grading, warranty terms and lead times are confirmed in writing before any order.\n\n"
            f"If this is useful, reply and I will send a quotation against your requirement. "
            f"If it is not, reply with 'unsubscribe' and I will remove you from this list.\n\n"
            f"Regards,\n{sender}"
        )
        return {"subject": subject, "body": body, "personalized": True}

    def _reply(self, ctx: dict, rng: random.Random) -> dict:
        name = ctx.get("contact_name") or "there"
        terms = ctx.get("terms") or {}
        lines = [f"Hello {name},", "", "Thank you for your reply."]
        price = ctx.get("indicative_unit_price_usd")
        qty = ctx.get("quantity")
        if price:
            lines.append(
                f"Our indicative price is USD {price} per unit"
                + (f" for {qty} units" if qty else "")
                + f", valid for {ctx.get('price_validity_days', 14)} days and subject to final confirmation "
                "of quantity, specification and delivery terms."
            )
        if terms.get("lead_time_days"):
            lines.append(f"Expected lead time is about {terms['lead_time_days']} days from order confirmation.")
        if terms.get("condition"):
            lines.append(f"Condition: {terms['condition']}.")
        lines += ["", "I will confirm anything else you need in writing.", "", f"Regards,\n{ctx.get('sender_name', 'NEXUS Sourcing')}"]
        return {"subject": f"Re: {str(ctx.get('product_category', 'your enquiry')).replace('_', ' ')}", "body": "\n".join(lines)}

    @staticmethod
    def _slug(name: str) -> str:
        return "".join(ch for ch in name.lower() if ch.isalnum())[:40]

    def _supplier_discovery(self, ctx: dict, rng: random.Random) -> dict:
        category = ctx.get("product_category")
        return {"suppliers": [
            {"name": s["name"], "country": s["country"], "website_url": f"https://{self._slug(s['name'])}.example/",
             "supplier_type": "distributor", "evidence_quote": f"wholesale {str(category).replace('_', ' ')}"}
            for s in fixtures.SUPPLIERS if category in s["categories"]
        ]}

    def _supplier_profile(self, ctx: dict, rng: random.Random) -> dict:
        category = ctx.get("product_category")
        name = ctx.get("supplier") or "supplier"
        return {
            "products": [f"wholesale {str(category).replace('_', ' ')}"],
            "certifications": fixtures.SUPPLIER_CERTIFICATIONS.get(category, []),
            "export_evidence": ["we export to East Africa"],
            "address": "Free Zone warehouse, unit 12",
            "contact": {"email": f"sales@{self._slug(name)}.example", "full_name": None, "role": None},
        }

    def _supplier_rfq(self, ctx: dict, rng: random.Random) -> dict:
        items = ctx.get("items") or [{}]
        item = items[0]
        greeting = f"Dear {ctx.get('contact_name')}," if ctx.get("contact_name") else "Dear Sales Team,"
        if ctx.get("follow_up_number"):
            body = (f"{greeting}\n\nFollowing up on our request for quotation. We would still welcome your "
                    f"prices and terms.\n\nRegards,\n{ctx.get('sender_name')}")
            return {"subject": "Following up: request for quotation", "body": body}
        docs = ctx.get("documents_needed") or []
        lines = [
            greeting, "",
            f"{ctx.get('our_business')} is sourcing {item.get('description')} for buyers in "
            f"{', '.join(item.get('buyer_countries') or ['East Africa'])}.",
            f"Please quote for about {item.get('quantity')} units per order, delivery {ctx.get('delivery')}.",
            "Please include: " + "; ".join(ctx.get("please_quote") or []) + ".",
        ]
        if ctx.get("license_statement"):
            lines.append(f"{ctx['license_statement']}.")
        if docs:
            lines.append("Please confirm you can provide: " + ", ".join(docs) + ".")
        lines += ["This is a request for prices, not an order.", "",
                  "If you would rather not receive requests from us, reply 'unsubscribe'.", "",
                  f"Regards,\n{ctx.get('sender_name')}"]
        return {"subject": "Request for quotation", "body": "\n".join(lines)}

    def _supplier_classify(self, ctx: dict, rng: random.Random) -> dict:
        text = (ctx.get("reply_text") or "").lower()
        if "unsubscribe" in text or "remove me" in text:
            return {"category": "unsubscribe", "confidence": 0.9}
        if "not able to supply" in text or "do not stock" in text:
            return {"category": "not_supplying", "confidence": 0.9}
        if "per unit" in text and "usd" in text:
            return {"category": "quote", "confidence": 0.9}
        if "?" in text:
            return {"category": "question", "confidence": 0.8}
        return {"category": "other", "confidence": 0.4}

    def _supplier_quote(self, ctx: dict, rng: random.Random) -> dict:
        import re as _re

        text = ctx.get("email_text") or ""

        def grab(pattern: str):
            match = _re.search(pattern, text, _re.I)
            return match.group(1) if match else None

        price = grab(r"USD\s*([\d.]+)\s*per unit")
        if not price:
            return {"lines": []}
        docs = grab(r"Documents:\s*([^.]+)\.")
        return {"lines": [{
            "product_name": f"{str(ctx.get('product_category')).replace('_', ' ')} (as quoted)",
            "unit_price": float(price), "currency": "USD",
            "moq": grab(r"MOQ\s*(\d+)"), "quantity_available": grab(r"(\d+) units available"),
            "lead_time_days": grab(r"lead time (\d+) days"), "incoterm": "FOB",
            "payment_terms": grab(r"Payment:\s*([^.]+)\."),
            "documents": [d.strip() for d in docs.split(",")] if docs else [],
        }]}

    def _classify(self, ctx: dict, rng: random.Random) -> dict:
        text = (ctx.get("reply_text") or "").lower()
        rules = [
            ("unsubscribe", ["unsubscribe", "remove me", "do not contact"]),
            ("complaint", ["reporting this", "unsolicited and unwelcome", "spam"]),
            ("regulatory_issue", ["regulator", "registration", "licence", "license"]),
            ("rfq", ["rfq", "formal requirement", "tender"]),
            ("price_request", ["pricing", "best price", "quote for"]),
            ("negotiation", ["above our budget", "improve terms", "discount"]),
            ("information_request", ["more detail", "specifications", "send more"]),
            ("wrong_contact", ["not the right person", "procurement handles"]),
            ("not_interested", ["not looking", "not interested", "no thank"]),
            ("interested", ["relevant to us", "reviewing options", "interested"]),
        ]
        for label, needles in rules:
            if any(n in text for n in needles):
                return {"category": label, "confidence": 0.9, "notes": f"matched phrase for {label}"}
        return {"category": "other", "confidence": 0.4, "notes": "no decisive signal"}

    def _learning(self, ctx: dict, rng: random.Random) -> dict:
        return {
            "observations": ["performance varies by product category and country"],
            "recommended_changes": [],
            "confidence": 0.5,
        }

    def _landing_copy(self, ctx: dict, rng: random.Random) -> dict:
        from app.site.pages import template_content

        return template_content(ctx) if ctx.get("category") else {}

    def _ad_copy(self, ctx: dict, rng: random.Random) -> dict:
        from app.ads.copy import KEYWORDS, template

        facts = ctx.get("facts") or {}
        if not facts.get("category"):
            return {"variants": {}, "keywords": []}
        variants = {a: template(ctx.get("platform", "google"), facts, a) for a in ctx.get("angles") or []}
        extra = [k.format(country=facts["country"].lower()) for k in KEYWORDS.get(facts["category"], [])][:3]
        return {"variants": variants, "keywords": extra + ["free laptops"]}

    HANDLERS = {
        "market_research": "_market_research",
        "prospect_discovery": "_prospects",
        "company_intelligence": "_company_intelligence",
        "decision_maker_discovery": "_decision_makers",
        "qualification": "_qualification",
        "sourcing": "_sourcing",
        "sales_strategy": "_strategy",
        "outreach_copy": "_outreach",
        "reply_draft": "_reply",
        "supplier_discovery": "_supplier_discovery",
        "supplier_profile": "_supplier_profile",
        "supplier_rfq_copy": "_supplier_rfq",
        "supplier_reply_classification": "_supplier_classify",
        "supplier_quote_extraction": "_supplier_quote",
        "response_classification": "_classify",
        "learning_review": "_learning",
        "landing_copy": "_landing_copy",
        "ad_copy": "_ad_copy",
    }

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        rng = _rng(request)
        if self.failure_rate and rng.random() < self.failure_rate:
            raise ProviderUnavailable(f"{self.name} simulated failure", task_type=request.task_type)
        handler_name = self.HANDLERS.get(request.task_type)
        if handler_name is None:
            payload: dict[str, Any] = {"echo": request.task_type, "notes": "no mock handler"}
        else:
            payload = getattr(self, handler_name)(request.context, rng)

        text = json.dumps(payload)
        if self.malformed_rate and rng.random() < self.malformed_rate:
            text = text[: max(1, len(text) // 2)]  # truncated output

        in_tokens = estimate_tokens(request.prompt) + estimate_tokens(request.system or "")
        out_tokens = estimate_tokens(text)
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)
        return ModelResponse(
            text=text,
            provider=self.name,
            model=f"{self.name}-{request.tier.value}",
            tier=request.tier,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cost_usd=price(request.tier, in_tokens, out_tokens),
            latency_ms=self.latency_ms,
        )


class AlwaysFailingProvider(BaseProvider):
    """Used to prove routing fallback and provider-health handling."""

    def __init__(self, name: str = "broken") -> None:
        self.name = name
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        raise ProviderUnavailable(f"{self.name} is down", task_type=request.task_type)
