"""Supplier side: find suppliers, check them out, ask them for quotes, read the quotes.

Flow
----
supplier_research  search the web and supplier directories for organisations
                   that sell a product line; every name must appear in a
                   retrieved source (same grounding rules as buyer research).
supplier_profile   read the supplier's own website: products, claimed
                   certifications, export evidence, address, published
                   contact. Score it and flag red flags in code.
supplier_rfq       e-mail qualified suppliers a request for quotation built
                   from live buyer demand; up to N follow-ups, threaded.
(response agent)   supplier replies are classified; a quote is read into
                   draft catalogue offers (every price must appear in the
                   e-mail) and waits in review until the operator activates it.

Nothing on this side commits to buying: an RFQ asks for prices. Claimed
certifications are recorded as unverified claims; documents are checked by
the operator before a regulated offer is activated.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.commercial import catalogue
from app.commercial import settings as commercial
from app.core.context import RunContext
from app.core.ids import stable_key
from app.core.interfaces import ActionRequest, AgentResult
from app.core.types import (
    ActionKind,
    Decision,
    EvidenceKind,
    ModelTier,
    OpportunityStage,
    ProductCategory,
    REGULATED_CATEGORIES,
)
from app.database.models import Company, Contact, Interaction, Message, Opportunity, ReviewItem, SupplierOffer, SupplierRFQ

SUPPLIER_QUERIES = {
    ProductCategory.LAPTOP.value: [
        "{region} wholesale refurbished business laptops exporter",
        "{region} ITAD refurbished laptops bulk supplier Dell Latitude HP EliteBook",
    ],
    ProductCategory.IPHONE.value: [
        "{region} wholesale used iPhones grade A bulk exporter",
        "{region} refurbished iPhone wholesaler",
    ],
    ProductCategory.MEDICAL.value: [
        "{region} refurbished medical equipment exporter",
        "{region} hospital equipment distributor export to Africa",
    ],
    ProductCategory.PHARMA.value: [
        "{region} WHO-GMP pharmaceutical manufacturer exporter Africa",
        "{region} generic medicines exporter East Africa",
    ],
    ProductCategory.SERVER_IT.value: [
        "{region} refurbished servers networking equipment wholesale exporter",
        "{region} used enterprise IT equipment reseller export",
    ],
}

RELEVANT_CERTIFICATIONS = {
    ProductCategory.MEDICAL.value: ("iso 13485", "ce mark", "ce certified", "fda", "iso 9001"),
    ProductCategory.PHARMA.value: ("who-gmp", "who gmp", "gmp", "pic/s", "prequalif", "iso 9001"),
    ProductCategory.LAPTOP.value: ("r2", "e-stewards", "iso 9001", "iso 14001", "microsoft authorized refurbisher", "rios"),
    ProductCategory.IPHONE.value: ("r2", "e-stewards", "iso 9001", "iso 14001"),
    ProductCategory.SERVER_IT.value: ("r2", "e-stewards", "iso 9001", "iso 14001", "rios"),
}

FREE_MAIL = ("gmail.com", "yahoo.", "hotmail.", "outlook.com", "live.com", "qq.com", "163.com", "126.com", "aol.com", "icloud.com", "mail.ru")
SUPPLIER_PATHS = ("/", "/about", "/about-us", "/products", "/contact", "/contact-us", "/certifications", "/quality")
SUPPLIER_MAILBOX_PRIORITY = ("export", "sales", "trade", "orders", "info", "contact", "enquiries", "inquiries")
CLOSED_STATUSES = {"blocked", "rejected", "declined", "unresponsive"}
ISO_CURRENCIES = {"USD", "EUR", "GBP", "AED", "CNY", "INR", "KES", "HKD", "JPY"}


def _words(category: str) -> str:
    from app.agents.grounded import CATEGORY_WORDS

    return CATEGORY_WORDS.get(category, category)


def _number_in(value: Any, text: str) -> bool:
    """True if the number appears in the text (ignoring thousands separators)."""
    try:
        target = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return False
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            if abs(float(raw.replace(",", "")) - target) < 1e-9:
                return True
        except ValueError:
            continue
    return False


def best_contact(ctx: RunContext, company: Company) -> Contact | None:
    return ctx.session.scalar(
        select(Contact)
        .where(Contact.company_id == company.id, Contact.opted_out.is_(False), Contact.bounced.is_(False),
               Contact.email.is_not(None))
        .order_by(Contact.confidence.desc(), Contact.created_at.desc())
    )


def score_supplier(category: str, profile: dict[str, Any], contact_email: str | None, own_domain: str | None) -> tuple[float, list[str]]:
    """Deterministic supplier score (0-1) and red flags."""
    flags: list[str] = []
    products = profile.get("products") or []
    certs = [c.lower() for c in profile.get("certifications") or []]
    exports = profile.get("export_evidence") or []
    address = profile.get("address")
    if not own_domain:
        flags.append("no own website")
    if not products:
        flags.append(f"no evidence it sells {category.replace('_', ' ')}")
    if contact_email and any(contact_email.endswith("@" + d) or ("@" + d) in contact_email for d in FREE_MAIL):
        flags.append("free webmail address only")
    if not address:
        flags.append("no business address published")
    relevant = RELEVANT_CERTIFICATIONS.get(category, ())
    cert_hit = any(any(r in c for r in relevant) for c in certs)
    contact_quality = 0.0 if not contact_email else (0.5 if "free webmail address only" in flags else 1.0)
    score = (
        0.35 * (1.0 if products else 0.0)
        + 0.20 * (1.0 if exports else 0.0)
        + 0.20 * (1.0 if cert_hit else (0.3 if certs else 0.0))
        + 0.15 * contact_quality
        + 0.10 * (1.0 if address else 0.0)
        - 0.10 * len([f for f in flags if f != "no business address published"])
    )
    return round(max(0.0, min(1.0, score)), 3), flags


def demand_for(ctx: RunContext, category: str) -> dict[str, Any]:
    """What our buyers need: typical order size and destination countries."""
    open_stages = {
        OpportunityStage.TARGETED.value, OpportunityStage.OUTREACH.value, OpportunityStage.FOLLOW_UP.value,
        OpportunityStage.RESPONSE.value, OpportunityStage.NEGOTIATION.value, OpportunityStage.COMMERCIAL_REVIEW.value,
        OpportunityStage.SCORED.value, OpportunityStage.QUALIFIED.value,
    }
    quantities, countries = [], set()
    for opp in ctx.session.scalars(
        select(Opportunity).where(Opportunity.product_category == category, Opportunity.stage.in_(open_stages))
    ):
        qty = (opp.economics or {}).get("quantity") or (opp.qualification or {}).get("order_potential_units")
        if qty:
            quantities.append(int(qty))
        company = ctx.session.get(Company, opp.company_id)
        if company and company.country:
            countries.add(company.country)
    simulation = ctx.settings.nexus_mode == "simulation"
    typical = commercial.typical_order_qty(ctx.session, category, simulation)
    quantity = int(sorted(quantities)[len(quantities) // 2]) if quantities else typical
    return {
        "quantity_per_order": quantity,
        "open_opportunities": len(quantities),
        "destination_countries": sorted(countries) or commercial.get(ctx.session)["target_markets"].get(category, [])[:4],
    }


# ============================================================ research


class SupplierResearchAgent(BaseAgent):
    name = "supplier_research"
    task_type = "supplier_discovery"
    tier = ModelTier.BULK
    complexity = 0.45

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        category = task_input["product_category"]
        limit = int(task_input.get("limit", 8))
        settings = commercial.get(ctx.session)
        regions = task_input.get("regions") or settings["supplier_regions"].get(category, [])[:4]
        if getattr(ctx.research, "live", False):
            proposals, docs, cost = self._search(ctx, category, regions, settings)
        else:
            data, cost = self.ask(
                ctx,
                f"List suppliers of {category}. Return {{'suppliers':[{{'name','country','website_url','supplier_type','evidence_quote'}}]}}.",
                {"product_category": category, "regions": regions},
            )
            proposals, docs = data.get("suppliers") or [], {}

        from app.agents.grounded import _doc_text
        from app.tools.research import appears, domain_of, is_aggregator

        created = known = rejected = 0
        next_tasks, evidence = [], []
        for item in proposals:
            if created + known >= limit:
                break
            name = (item.get("name") or "").strip()
            if not name:
                continue
            doc = docs.get(item.get("source_id") or "") if docs else None
            if docs and (doc is None or not appears(name, _doc_text(doc))):
                rejected += 1
                continue
            website = item.get("website_url") or ""
            domain = None
            if website and not is_aggregator(website):
                candidate = domain_of(website)
                if not docs or candidate == doc.domain or appears(candidate, _doc_text(doc)):
                    domain = candidate
            country = item.get("country")
            if docs and country and not appears(country, _doc_text(doc)):
                country = item.get("_region")
            company, is_new = ctx.memory.upsert_company(
                name=name, domain=domain, country=country, kind="supplier",
                segment=(item.get("supplier_type") or None),
                source=(doc.url if doc else item.get("source") or "simulated directory")[:200],
                profile={"categories": [category]},
            )
            categories = sorted({*(company.profile or {}).get("categories", []), category})
            company.profile = {**(company.profile or {}), "categories": categories}
            created += int(is_new)
            known += int(not is_new)
            evidence.append(
                self.evidence(
                    f"{company.name} presented as a supplier of {category}",
                    source=doc.url if doc else "simulated directory",
                    kind=EvidenceKind.UNVERIFIED_CLAIM, confidence=0.45,
                    subject_type="company", subject_id=company.id, source_type="web" if doc else "simulation",
                )
            )
            if is_new or company.status == "lead":
                next_tasks.append({"agent": "supplier_profile",
                                   "input": {"company_id": company.id, "product_category": category}, "priority": 58})
        self.persist_evidence(ctx, evidence)
        return self.ok(
            output={"created": created, "known": known, "rejected": rejected},
            cost_usd=cost, evidence=evidence, next_tasks=next_tasks,
            notes=[f"{created} new supplier leads, {known} known, {rejected} rejected as not in any source"],
        )

    def _search(self, ctx: RunContext, category: str, regions: list[str], settings: dict) -> tuple[list[dict], dict, float]:
        from app.tools.research import payload

        docs, doc_region = [], {}
        per_region = min(2, max(1, int(ctx.settings.research_max_queries_per_task)))
        for region in regions:
            for template in SUPPLIER_QUERIES.get(category, ["{region} " + category + " supplier"])[:per_region]:
                for doc in ctx.research.search(template.format(region=region), count=8):
                    if doc.id not in doc_region:
                        docs.append(doc)
                        doc_region[doc.id] = region
        for directory in (settings["directories"].get("supplier") or {}).get(category, [])[:3]:
            for doc in ctx.research.search(f"site:{directory} {_words(category)} supplier exporter", count=8):
                if doc.id not in doc_region:
                    docs.append(doc)
                    doc_region[doc.id] = None
        if not docs:
            return [], {}, 0.0
        data, cost = self.ask(
            ctx,
            (
                f"From these search results, list organisations that SELL {category} (manufacturers, "
                "distributors, refurbishers, wholesalers). Only organisations NAMED in a result. Give the exact "
                "name, the source_id, a verbatim quote showing they sell it, their country if the result states "
                "it, their own website if shown, and supplier_type. Return {'suppliers':[{'name','source_id',"
                "'evidence_quote','country','website_url','supplier_type'}]}."
            ),
            {"product_category": category, "results": payload(docs, 600)},
        )
        proposals = []
        for item in data.get("suppliers") or []:
            item = dict(item)
            item["_region"] = doc_region.get(item.get("source_id") or "")
            proposals.append(item)
        return proposals, {doc.id: doc for doc in docs}, cost


# ============================================================ profile


class SupplierProfileAgent(BaseAgent):
    name = "supplier_profile"
    task_type = "supplier_profile"
    tier = ModelTier.BULK
    complexity = 0.5

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        company = ctx.session.get(Company, task_input["company_id"])
        if company is None or company.kind != "supplier":
            return self.fail("supplier not found")
        if company.status in CLOSED_STATUSES:
            return self.ok(output={"skipped": company.status})
        category = task_input.get("product_category") or ((company.profile or {}).get("categories") or [None])[0]
        if getattr(ctx.research, "live", False):
            profile, contact_info, cost, source_url = self._grounded(ctx, company, category)
        else:
            data, cost = self.ask(
                ctx,
                "Profile this supplier. Return {'products':[],'certifications':[],'export_evidence':[],"
                "'address','contact':{'email','full_name','role'}}.",
                {"supplier": company.name, "domain": company.domain, "product_category": category},
            )
            profile = {k: data.get(k) for k in ("products", "certifications", "export_evidence", "address")}
            contact_info = data.get("contact") or {}
            source_url = f"https://{company.domain}/contact" if company.domain else "simulated profile"

        email = (contact_info.get("email") or "").strip().lower() or None
        score, flags = score_supplier(category, profile, email, company.domain)
        company.profile = {**(company.profile or {}), **{k: v for k, v in profile.items() if v}, "red_flags": flags}
        company.score = score
        min_score = float(commercial.get(ctx.session)["supplier_min_score"])
        evidence = [
            self.evidence(
                f"{company.name} claims certification: {cert}", source=source_url,
                kind=EvidenceKind.UNVERIFIED_CLAIM, confidence=0.4,
                subject_type="company", subject_id=company.id, source_type="web",
            )
            for cert in (profile.get("certifications") or [])[:6]
        ]
        self.persist_evidence(ctx, evidence)

        next_tasks = []
        if email:
            named = bool(contact_info.get("full_name"))
            contact = ctx.memory.upsert_contact(
                company_id=company.id,
                full_name=contact_info.get("full_name") or "Sales team",
                role=contact_info.get("role") or "published sales mailbox",
                email=email, confidence=0.7 if named else 0.6, source=source_url[:200], verified=True,
            )
            if contact.company_id != company.id:
                email = None  # address already belongs to another organisation: do not reuse it
        if email and score >= min_score:
            company.status = "qualified"
            next_tasks.append({"agent": "supplier_rfq",
                               "input": {"company_id": company.id, "product_category": category}, "priority": 55})
        else:
            company.status = "rejected" if not email else "lead"
        ctx.session.flush()
        return self.ok(
            output={"score": score, "red_flags": flags, "status": company.status, "email": email},
            cost_usd=cost, evidence=evidence, next_tasks=next_tasks,
        )

    def _grounded(self, ctx: RunContext, company: Company, category: str) -> tuple[dict, dict, float, str]:
        from app.agents.grounded import _doc_text, _find_website, _on_site
        from app.tools.fetch import extract_emails
        from app.tools.research import appears, email_appears, payload

        if not company.domain:
            company.domain = _find_website(ctx, company)
        if not company.domain:
            return {}, {}, 0.0, company.source or ""
        pages = [d for d in ctx.research.fetch_site(company.domain, SUPPLIER_PATHS,
                                                    limit=int(ctx.settings.research_max_pages_per_task) + 2)
                 if _on_site(d, company.domain)]
        if not pages:
            return {}, {}, 0.0, f"https://{company.domain}/"
        emails = sorted({e for d in pages for e in extract_emails(d.text)})
        data, cost = self.ask(
            ctx,
            (
                f"Using only these pages of {company.name}'s own website, extract evidence for a supplier check "
                f"for {category}. Every item must be a verbatim quote with its source_id. The contact e-mail MUST "
                "be one of 'published_emails'; give a person's name only if written on the page. Return "
                "{'products':[{'quote','source_id'}],'certifications':[{'name','quote','source_id'}],"
                "'export_evidence':[{'quote','source_id'}],'address':{'quote','source_id'},"
                "'contact':{'email','full_name','role','source_id'}}."
            ),
            {"supplier": company.name, "product_category": category, "published_emails": emails,
             "pages": payload(pages)},
        )
        lookup = {d.id: d for d in pages}

        def verified(items: list[dict]) -> list[str]:
            out = []
            for item in items or []:
                doc = lookup.get(item.get("source_id") or "")
                quote = (item.get("quote") or "").strip()
                if doc is not None and appears(quote, _doc_text(doc)):
                    out.append((item.get("name") or quote)[:200])
            return out

        address = data.get("address") or {}
        address_doc = lookup.get(address.get("source_id") or "")
        profile = {
            "products": verified(data.get("products")),
            "certifications": verified(data.get("certifications")),
            "export_evidence": verified(data.get("export_evidence")),
            "address": address.get("quote") if address_doc is not None and appears(address.get("quote"), _doc_text(address_doc)) else None,
        }
        contact = data.get("contact") or {}
        email = (contact.get("email") or "").strip().lower()
        doc = lookup.get(contact.get("source_id") or "")
        if email in emails and doc is not None and email_appears(email, doc.text):
            name = contact.get("full_name") if contact.get("full_name") and appears(contact.get("full_name"), doc.text) else None
            return profile, {"email": email, "full_name": name, "role": contact.get("role") if name else None}, cost, doc.url
        for prefix in SUPPLIER_MAILBOX_PRIORITY:
            for candidate in emails:
                if candidate.split("@")[0].lower().startswith(prefix):
                    source = next((d.url for d in pages if email_appears(candidate, d.text)), pages[0].url)
                    return profile, {"email": candidate}, cost, source
        if emails:
            source = next((d.url for d in pages if email_appears(emails[0], d.text)), pages[0].url)
            return profile, {"email": emails[0]}, cost, source
        return profile, {}, cost, pages[0].url


# ============================================================ RFQ


class SupplierRFQAgent(BaseAgent):
    name = "supplier_rfq"
    task_type = "supplier_rfq_copy"
    tier = ModelTier.BULK
    complexity = 0.45

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        from app.agents.outreach import license_statement_for_category
        from app.execution.send import send_email

        rfq = ctx.session.get(SupplierRFQ, task_input["rfq_id"]) if task_input.get("rfq_id") else None
        company = ctx.session.get(Company, rfq.company_id if rfq else task_input.get("company_id"))
        if company is None or company.kind != "supplier":
            return self.fail("supplier not found")
        if company.status in CLOSED_STATUSES:
            return self.ok(output={"sent": False, "skipped": company.status})
        category = rfq.product_category if rfq else task_input["product_category"]
        step = int(task_input.get("step", rfq.followups_sent + 1 if rfq and rfq.status == "sent" else 0))
        contact = ctx.session.get(Contact, rfq.contact_id) if rfq and rfq.contact_id else best_contact(ctx, company)
        if contact is None:
            return self.ok(output={"sent": False}, notes=["no usable supplier contact"])

        settings = commercial.get(ctx.session)
        if rfq is None:
            period = ctx.now.strftime("%Y-%m")
            key = stable_key("rfq", company.id, category, period)
            rfq = ctx.session.scalar(select(SupplierRFQ).where(SupplierRFQ.dedupe_key == key))
            if rfq is not None and rfq.status not in ("drafted", "pending_approval"):
                return self.ok(output={"sent": False, "skipped": f"RFQ already {rfq.status} this month"})
            if rfq is None:
                demand = demand_for(ctx, category)
                rfq = SupplierRFQ(
                    dedupe_key=key, company_id=company.id, contact_id=contact.id, product_category=category,
                    items=[{"description": settings["rfq_products"].get(category, category),
                            "quantity": demand["quantity_per_order"],
                            "buyer_countries": demand["destination_countries"]}],
                    destination=settings["rfq_destination"], status="drafted",
                )
                ctx.session.add(rfq)
                ctx.session.flush()

        approved_id = task_input.get("approved_review_id")
        approved = ctx.session.get(ReviewItem, approved_id) if approved_id else None
        regulated = category in {c.value for c in REGULATED_CATEGORIES}
        licence = license_statement_for_category(ctx, category) if regulated else None
        if approved is not None and (approved.action_payload or {}).get("body"):
            copy, cost = {"subject": approved.action_payload.get("subject", ""), "body": approved.action_payload["body"]}, 0.0
        else:
            facts = {
                "supplier_name": company.name,
                "contact_name": None if (contact.role or "").startswith("published") else contact.full_name.split()[0],
                "our_business": ctx.settings.business_name or "our company",
                "sender_name": ctx.settings.email_sender_name or "NEXUS Sourcing",
                "items": rfq.items,
                "delivery": rfq.destination,
                "please_quote": ["unit price and currency", "quantity available and MOQ", "condition grading",
                                 "warranty", "lead time", "payment terms", "Incoterms (EXW/FOB and CIF Mombasa)",
                                 "price validity"],
                "documents_needed": settings["required_documents"].get(category, []) if regulated else [],
                "license_statement": licence,
                "follow_up_number": step,
            }
            copy, cost = self.ask(
                ctx,
                (
                    "Write a concise, professional request for quotation to this supplier using only these facts. "
                    "It is a request for prices, not an order. If follow_up_number > 0, write a short polite "
                    "follow-up to the earlier request. Include an opt-out line. Return {'subject','body'}."
                ),
                facts,
            )
        numbers = [item.get("quantity") for item in rfq.items if item.get("quantity")]
        request = ActionRequest(
            kind=ActionKind.SEND_SUPPLIER_RFQ,
            summary=f"RFQ to {company.name} for {category}" + (f" (follow-up {step})" if step else ""),
            payload={
                "contact_id": contact.id, "company_id": company.id, "to": contact.email,
                "subject": copy.get("subject") or f"Request for quotation: {category.replace('_', ' ')}",
                "body": copy.get("body") or "", "personalized": True, "product_category": category,
                "country": company.country, "step": step, "rfq_id": rfq.id, "counterparty": "supplier",
                "allowed_facts": {"numbers": numbers, "license_verified": licence is not None},
                "approved_review_id": approved_id,
            },
            estimated_cost_usd=0.01, cost_category="email",
            idempotency_key=stable_key("rfq_send", rfq.id, step),
        )
        decision = ctx.authorize(request)
        if decision.decision == Decision.ESCALATE:
            rfq.status = "pending_approval"
            return self.ok(output={"sent": False, "escalated": True, "reasons": decision.reasons}, cost_usd=cost)
        if decision.decision == Decision.BLOCK:
            if step == 0:
                rfq.status = "blocked"
            return self.ok(output={"sent": False, "blocked": True, "reasons": decision.reasons}, cost_usd=cost)

        first = ctx.session.get(Message, rfq.first_message_id) if rfq.first_message_id else None
        message = send_email(
            ctx, contact, request.payload["subject"], request.payload["body"], request.idempotency_key,
            step=step, fact_check={"validated": True}, product_category=category, counterparty="supplier",
            in_reply_to=first.provider_message_id if first else None,
        )
        if message.status != "sent":
            return self.fail(f"send failed: {message.error}", cost_usd=cost)
        if step == 0:
            rfq.first_message_id = message.id
        else:
            rfq.followups_sent = step
        rfq.status = "sent"
        rfq.last_sent_at = ctx.now
        rfq.next_followup_at = ctx.now + timedelta(days=ctx.settings.supplier_rfq_followup_days)
        company.status = "rfq_sent"
        ctx.session.flush()
        return self.ok(output={"sent": True, "rfq_id": rfq.id, "step": step}, cost_usd=cost,
                       notes=[f"RFQ sent to {contact.email}"])


# ============================================================ replies


def handle_supplier_reply(agent: BaseAgent, ctx: RunContext, message: Message, contact: Contact, company: Company) -> AgentResult:
    """Classify a supplier's reply; turn a quote into draft offers for review."""
    rfq = ctx.session.scalar(
        select(SupplierRFQ).where(SupplierRFQ.company_id == company.id).order_by(SupplierRFQ.created_at.desc())
    )
    data, cost = agent.ask(
        ctx,
        "Classify this supplier's reply to our request for quotation as one of: quote, question, not_supplying, "
        "unsubscribe, other. Return {'category','confidence'}.",
        {"reply_text": message.body, "supplier": company.name},
        task_type="supplier_reply_classification",
    )
    category = data.get("category") if data.get("category") in {"quote", "question", "not_supplying", "unsubscribe", "other"} else "other"
    message.status = "processed"
    ctx.session.add(Interaction(company_id=company.id, contact_id=contact.id, direction="inbound",
                                category=f"supplier_{category}", summary=message.body[:400]))
    if rfq is not None:
        rfq.reply_message_id = message.id
        rfq.next_followup_at = None
    notes = [f"supplier reply: {category}"]

    if category == "unsubscribe":
        ctx.memory.opt_out_contact(contact.id, reason="unsubscribe")
        if rfq is not None:
            rfq.status = "cancelled"
    elif category == "not_supplying":
        company.status = "declined"
        if rfq is not None:
            rfq.status = "declined"
    elif category == "quote":
        product_category = rfq.product_category if rfq else ((company.profile or {}).get("categories") or [None])[0]
        offers, extract_cost = extract_quote(agent, ctx, message, company, product_category, rfq)
        cost += extract_cost
        if rfq is not None:
            rfq.status = "quoted"
        company.status = "quoted"
        ctx.audit.record(
            "supplier_quote_received",
            summary=f"{company.name} quoted {len(offers)} line(s) for {product_category}",
            decision="escalate",
            review_key=stable_key("supplier_quote", message.id),
            reasons=["check the extracted prices and terms against the e-mail, then activate them"],
            action_kind="activate_offers",
            action_preview={
                "supplier": company.name, "from": contact.email, "their_message": message.body[:4000],
                "offer_ids": [o.id for o in offers],
                "offers": [catalogue.offer_as_dict(o, ctx.session) | {"currency": o.currency} for o in offers],
            },
        )
        notes.append(f"{len(offers)} draft offer(s) awaiting your check")
    else:
        if rfq is not None:
            rfq.status = "replied"
        ctx.audit.record(
            "supplier_question",
            summary=f"{company.name} replied to our RFQ ({category})",
            decision="escalate",
            review_key=stable_key("supplier_reply", message.id),
            reasons=["answer the supplier from your mailbox, then mark resolved"],
            action_preview={"supplier": company.name, "from": contact.email, "their_message": message.body[:4000]},
        )
    ctx.session.flush()
    return agent.ok(output={"category": category, "supplier": True}, cost_usd=cost, notes=notes)


def extract_quote(agent: BaseAgent, ctx: RunContext, message: Message, company: Company,
                  category: str | None, rfq: SupplierRFQ | None) -> tuple[list[SupplierOffer], float]:
    """Read quoted lines from the e-mail. Every price must appear in the e-mail text."""
    text = f"{message.subject or ''}\n{message.body}"
    data, cost = agent.ask(
        ctx,
        (
            "Extract every quoted product line from this supplier e-mail. Use numbers exactly as written; "
            "use null for anything not stated. Return {'lines':[{'product_name','condition','unit_price',"
            "'unit_price_high','currency','quantity_available','moq','incoterm','lead_time_days',"
            "'payment_terms','warranty','documents':[]}]}."
        ),
        {"email_text": text[:6000], "product_category": category},
        task_type="supplier_quote_extraction",
    )
    offers: list[SupplierOffer] = []
    if category is None:
        return offers, cost
    supplier = catalogue.upsert_supplier(ctx.session, name=company.name, country=company.country, categories=[category])
    for line in (data.get("lines") or [])[:10]:
        price = line.get("unit_price")
        if price is None or not _number_in(price, text):
            continue  # a price we cannot see in the e-mail is not recorded
        high = line.get("unit_price_high") if line.get("unit_price_high") and _number_in(line.get("unit_price_high"), text) else price
        currency = str(line.get("currency") or "USD").upper()[:3]
        if currency not in ISO_CURRENCIES:
            currency = "USD" if "usd" in text.lower() or "$" in text else "???"

        def stated(key: str) -> int | None:
            value = line.get(key)
            return int(float(value)) if value is not None and _number_in(value, text) else None

        offer = SupplierOffer(
            supplier_id=supplier.id,
            product_category=category,
            product_name=str(line.get("product_name") or category)[:300],
            condition=line.get("condition") or None,
            quantity_available=stated("quantity_available"),
            moq=stated("moq"),
            unit_cost_low_usd=float(price),
            unit_cost_high_usd=float(high),
            incoterm=line.get("incoterm") or None,
            lead_time_days=stated("lead_time_days"),
            payment_terms=line.get("payment_terms") or None,
            documents=[d for d in (line.get("documents") or []) if isinstance(d, str)],
            warranty=line.get("warranty") or None,
            source=f"e-mail from {company.name} ({message.from_address or 'supplier'}), message {message.id}"[:500],
            notes="read from supplier e-mail; check before activating",
            active=False,
            status="pending_review",
            rfq_id=rfq.id if rfq else None,
            currency=currency,
        )
        ctx.session.add(offer)
        offers.append(offer)
    ctx.session.flush()
    return offers, cost


def activate_offers(ctx: RunContext, offer_ids: list[str]) -> list[SupplierOffer]:
    offers = [o for o in (ctx.session.get(SupplierOffer, i) for i in offer_ids) if o is not None]
    foreign = [o for o in offers if o.currency != "USD"]
    if foreign:
        raise ValueError(
            f"{len(foreign)} line(s) are priced in {', '.join(sorted({o.currency for o in foreign}))}; "
            "convert to USD and add them in the Catalogue tab, then reject this item"
        )
    for offer in offers:
        offer.status = "active"
        offer.active = True
    ctx.session.flush()
    return offers
