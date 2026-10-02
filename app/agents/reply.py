"""Answering interested buyers: drafted by NEXUS, approved (and editable) by you.

When a buyer asks for prices, an RFQ response or more information, NEXUS
drafts the reply from facts it holds — the matched supplier offer, the deal
economics and a suggested price computed in code — and puts it in the review
queue. Nothing is sent until the operator approves; the operator can edit
the text first. A quote is a commercial commitment, so it never goes out
autonomously (policy rule R-REPLY-01).
"""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.agents.translate import back_translate, final_text, flatten
from app.core import languages
from app.commercial import settings as commercial
from app.core.context import RunContext
from app.core.ids import stable_key
from app.core.interfaces import ActionRequest, AgentResult
from app.core.types import ActionKind, Decision, ModelTier, OpportunityStage
from app.database.models import Company, Contact, Message, Opportunity, ReviewItem, SupplierOffer

QUOTE_VALIDITY_DAYS = 14


def suggest_pricing(ctx: RunContext, opportunity: Opportunity) -> dict[str, Any] | None:
    """A suggested unit price from landed cost, the margin floor and the price book.

    Deterministic; the model never picks a price. None when the deal has no
    economics yet (no offer or price reference matched).
    """
    econ = opportunity.economics or {}
    quantity = int(econ.get("quantity") or 0)
    landed = econ.get("landed_cost_usd")
    revenue = econ.get("revenue_usd")
    if not quantity or not landed or not revenue:
        return None
    min_margin = float(econ.get("min_margin_pct") or commercial.get(ctx.session)["min_margin_pct"].get(opportunity.product_category, 12.0))
    unit_landed_high = float(landed[1]) / quantity
    floor = unit_landed_high / (1 - min_margin / 100)
    book_low, book_high = float(revenue[0]) / quantity, float(revenue[1]) / quantity
    suggested = min(max(floor, book_low), book_high)
    note = "within the price book"
    if floor > book_high:
        suggested = floor
        margin_at_book_high = (book_high - unit_landed_high) / book_high * 100 if book_high else 0
        note = f"above the price book: at its high end the margin would only be {margin_at_book_high:.1f}%"
    unknowns = list(econ.get("unknowns") or [])
    return {
        "quantity": quantity,
        "unit_landed_cost_usd": round(unit_landed_high, 2),
        "min_margin_pct": min_margin,
        "floor_unit_price_usd": round(floor, 2),
        "price_book_unit_usd": [round(book_low, 2), round(book_high, 2)],
        "suggested_unit_price_usd": round(suggested, 2),
        "note": note + (f"; costs still unknown: {', '.join(unknowns)}" if unknowns else ""),
    }


def _offer_terms(ctx: RunContext, opportunity: Opportunity) -> dict[str, Any]:
    econ = opportunity.economics or {}
    terms: dict[str, Any] = {
        "condition": econ.get("condition"),
        "lead_time_days": econ.get("lead_time_days"),
    }
    offer = ctx.session.get(SupplierOffer, econ["offer_id"]) if econ.get("offer_id") else None
    if offer is not None:
        terms.update(
            product_name=offer.product_name,
            warranty=offer.warranty,
            payment_terms=offer.payment_terms,
            incoterm=offer.incoterm,
        )
    return {k: v for k, v in terms.items() if v not in (None, "")}


def draft_reply(agent: BaseAgent, ctx: RunContext, message: Message, opportunity: Opportunity,
                contact: Contact, category: str) -> bool:
    """Draft an answer and put it in review. Returns False if nothing could be drafted."""
    if not contact.email or contact.opted_out or contact.bounced:
        return False
    company = ctx.session.get(Company, opportunity.company_id)
    pricing = suggest_pricing(ctx, opportunity) if category in {"price_request", "rfq", "negotiation", "interested"} else None
    terms = _offer_terms(ctx, opportunity)
    facts: dict[str, Any] = {
        "their_message": message.body[:3000],
        "reply_category": category,
        "company_name": company.name if company else None,
        "contact_name": None if contact.role == "published role mailbox" else contact.full_name.split()[0],
        "product_category": opportunity.product_category,
        "quantity": (opportunity.economics or {}).get("quantity"),
        "terms": terms,
        "sender_name": ctx.settings.email_sender_name or "NEXUS Sourcing",
    }
    if pricing:
        facts["indicative_unit_price_usd"] = pricing["suggested_unit_price_usd"]
        facts["price_validity_days"] = QUOTE_VALIDITY_DAYS
    market_language = languages.language_for(ctx.session, company.country if company else None)
    facts["market_language"] = market_language
    data, cost = agent.ask(
        ctx,
        (
            "Draft a reply to this buyer's message. Answer what they asked using ONLY the facts provided. "
            "If an indicative unit price is provided, present it as indicative, in USD, valid for the stated "
            "number of days and subject to final confirmation of quantity, specification and delivery terms. "
            "Do not invent specifications, certifications, stock levels, delivery dates or discounts. If they "
            "ask for something not in the facts, say you will confirm it. Write the reply in the language "
            "their message is written in (English, Arabic, Turkish or Hebrew); keep product and brand names "
            "and 'USD' in Latin letters and use Western digits. Return {'subject','body','language' (en, ar, "
            "tr or he),'their_message_english' (a literal English translation of their message, or null if "
            "it is already in English)}."
        ),
        facts,
        task_type="reply_draft",
        tier=ModelTier.REASONING,
    )
    body = (data.get("body") or "").strip()
    if not body:
        return False
    language = data.get("language") if languages.supported(data.get("language")) else market_language
    subject = (data.get("subject") or f"Re: {message.subject or 'your enquiry'}")[:300]
    english: dict = {}
    if language != languages.ENGLISH:
        english, _ = back_translate(agent, ctx, {"subject": subject, "body": body}, language)
    numbers = [n for n in (
        facts.get("quantity"), terms.get("lead_time_days"), facts.get("indicative_unit_price_usd"),
        facts.get("price_validity_days"),
    ) if n]
    request = ActionRequest(
        kind=ActionKind.SEND_REPLY,
        summary=f"reply to {contact.full_name} at {company.name if company else 'buyer'} ({category})",
        payload={
            "contact_id": contact.id,
            "company_id": opportunity.company_id,
            "opportunity_id": opportunity.id,
            "to": contact.email,
            "subject": subject,
            "body": body,
            "language": language,
            "english_check": flatten(english) or None,
            "subject_english": english.get("subject"),
            "body_english": english.get("body"),
            "their_message_english": data.get("their_message_english") or None,
            "personalized": True,
            "product_category": opportunity.product_category,
            "country": company.country if company else None,
            "regulatory_checked": "missing_documentation" not in (opportunity.compliance_flags or []),
            "allowed_facts": {"numbers": numbers, "regulatory_verified": False, "relationship_verified": False},
            "in_reply_to_message_id": message.id,
            "their_message": message.body[:3000],
            "pricing": pricing,
        },
        estimated_cost_usd=0.01,
        cost_category="email",
        idempotency_key=stable_key("reply", message.id),
        objective_id=opportunity.objective_id,
        opportunity_id=opportunity.id,
    )
    decision = ctx.authorize(request)
    return decision.decision == Decision.ESCALATE


class ReplyAgent(BaseAgent):
    """Sends a reply the operator approved in review."""

    name = "reply"
    task_type = "reply_send"
    tier = ModelTier.BULK

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        from app.agents.outreach import OutreachAgent

        item = ctx.session.get(ReviewItem, task_input.get("review_id") or "")
        if item is None or item.status != "approved" or item.action_kind != "send_reply":
            return self.fail("no approved reply for this task")
        payload = item.action_payload or {}
        opportunity = ctx.session.get(Opportunity, item.opportunity_id) if item.opportunity_id else None
        contact = ctx.session.get(Contact, payload.get("contact_id")) if payload.get("contact_id") else None
        if opportunity is None or contact is None:
            return self.fail("opportunity or contact missing")
        inbound = ctx.session.get(Message, payload.get("in_reply_to_message_id")) if payload.get("in_reply_to_message_id") else None

        language = payload.get("language") or languages.ENGLISH
        subject, body, _ = final_text(self, ctx, payload)
        request = ActionRequest(
            kind=ActionKind.SEND_REPLY,
            summary=f"approved reply to {contact.full_name}",
            payload={
                "contact_id": contact.id,
                "company_id": opportunity.company_id,
                "opportunity_id": opportunity.id,
                "to": contact.email,
                "subject": subject,
                "body": body,
                "language": language,
                "personalized": True,
                "product_category": opportunity.product_category,
                "country": payload.get("country"),
                "regulatory_checked": payload.get("regulatory_checked", False),
                "allowed_facts": payload.get("allowed_facts") or {"numbers": []},
                "approved_review_id": item.id,
            },
            estimated_cost_usd=0.01,
            cost_category="email",
            idempotency_key=item.review_key,
            objective_id=opportunity.objective_id,
            opportunity_id=opportunity.id,
        )
        decision = ctx.authorize(request)
        if decision.decision != Decision.ALLOW:
            return self.ok(output={"sent": False, "reasons": decision.reasons}, notes=["approved reply was blocked"])
        message = OutreachAgent()._deliver(
            ctx, opportunity, contact, request.payload["subject"], request.payload["body"],
            item.review_key, -1, {"validated": True, "operator_approved": True, "language": language},
            in_reply_to=inbound.provider_message_id if inbound is not None else None, language=language,
        )
        if message.status != "sent":
            return self.fail(f"send failed: {message.error}")
        ctx.memory.transition(opportunity, OpportunityStage.NEGOTIATION, "operator-approved reply sent", actor="operator")
        from app.site.leads import sync_lead_status

        sync_lead_status(ctx.session, opportunity)
        return self.ok(output={"sent": True, "message_id": message.id}, notes=[f"reply sent to {contact.email}"])
