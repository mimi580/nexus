"""Inbound enquiries: capture, attribute, acknowledge, and turn into deals.

A person who fills in the form has asked us to contact them, so NEXUS answers
fast: an acknowledgement e-mail goes out on the next worker cycle, the
operator is alerted, the deal is costed from the catalogue, and a quotation is
drafted for approval. Attribution (which ad, which variant, which click id)
is kept so the ad optimiser and the platforms learn which ads bring buyers.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.ids import stable_key
from app.core.interfaces import ActionRequest, AgentResult
from app.core.types import ActionKind, Decision, ModelTier, OpportunityStage
from app.database.models import AdCampaign, AdVariant, LandingPage, Lead, Message, Opportunity, PageEvent

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MAX_ENQUIRIES_PER_VISITOR_HOUR = 5
BOT_MARKERS = ("bot", "crawl", "spider", "preview", "facebookexternalhit", "slurp", "headless")


class LeadRejected(ValueError):
    pass


def visitor_hash(settings: Any, ip: str, user_agent: str, day: str) -> str:
    """A daily-rotating pseudonymous visitor id; raw IPs are never stored."""
    salt = settings.unsubscribe_secret or "nexus"
    return hashlib.sha256(f"{salt}|{day}|{ip}|{user_agent}".encode()).hexdigest()[:32]


def is_bot(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    return not ua or any(marker in ua for marker in BOT_MARKERS)


def resolve_attribution(session, params: dict[str, str]) -> dict[str, Any]:
    platform = "organic"
    if params.get("gclid") or params.get("utm_source") == "google":
        platform = "google"
    elif params.get("fbclid") or params.get("utm_source") in ("meta", "facebook", "instagram"):
        platform = "meta"
    elif params.get("utm_source"):
        platform = params["utm_source"][:20]
    campaign = session.get(AdCampaign, params["nx"]) if params.get("nx") else None
    variant_key = None
    if campaign is not None:
        ad_id = params.get("nv") or params.get("utm_content")
        if ad_id:
            variant = session.scalar(select(AdVariant).where(AdVariant.campaign_id == campaign.id,
                                                             (AdVariant.external_id == ad_id) | (AdVariant.key == ad_id)))
            variant_key = variant.key if variant is not None else None
    attribution = {k: v[:300] for k, v in params.items() if v}
    if params.get("fbclid"):
        attribution["fbc"] = f"fb.1.{int(time.time() * 1000)}.{params['fbclid']}"
    return {"platform": platform, "campaign_id": campaign.id if campaign else None,
            "variant_key": variant_key, "attribution": attribution}


def record_event(session, page: LandingPage, kind: str, params: dict[str, str], visitor: str) -> None:
    attr = resolve_attribution(session, params)
    if kind == "view":
        recent = session.scalar(select(func.count()).select_from(PageEvent).where(
            PageEvent.landing_page_id == page.id, PageEvent.kind == "view", PageEvent.visitor_hash == visitor))
        if recent:
            return  # one view per visitor per day (the hash rotates daily)
    session.add(PageEvent(landing_page_id=page.id, kind=kind, campaign_id=attr["campaign_id"],
                          variant_key=attr["variant_key"], platform=attr["platform"], visitor_hash=visitor))
    session.flush()


def intake(ctx: RunContext, page: LandingPage, form: dict[str, str], visitor: str) -> Lead:
    """Validate and store an enquiry. Spam is stored as spam (and ignored), not rejected loudly."""
    if (form.get("website") or "").strip():
        lead = Lead(landing_page_id=page.id, full_name="(honeypot)", email="spam@invalid", product_category=page.product_category,
                    status="spam", visitor_hash=visitor, message="")
        ctx.session.add(lead)
        ctx.session.flush()
        return lead
    since = ctx.now - timedelta(hours=1)
    recent = ctx.session.scalar(select(func.count()).select_from(Lead).where(
        Lead.visitor_hash == visitor, Lead.created_at >= since)) or 0
    if recent >= MAX_ENQUIRIES_PER_VISITOR_HOUR:
        raise LeadRejected("too many enquiries; please try again later")
    name = " ".join((form.get("full_name") or "").split())[:200]
    organisation = " ".join((form.get("organisation") or "").split())[:300]
    email = (form.get("email") or "").strip().lower()[:200]
    if not name or not organisation:
        raise LeadRejected("please give your name and organisation")
    if not EMAIL_RE.match(email):
        raise LeadRejected("please give a valid e-mail address")
    if form.get("consent") != "yes":
        raise LeadRejected("please agree to be contacted about this enquiry")
    quantity = None
    if (form.get("quantity") or "").strip():
        try:
            quantity = int(float(form["quantity"]))
        except ValueError as exc:
            raise LeadRejected("quantity must be a number") from exc
        if not 1 <= quantity <= 1_000_000:
            raise LeadRejected("quantity must be between 1 and 1,000,000")
    message = (form.get("message") or "").strip()[:4000]
    if form.get("product"):
        message = f"Product: {form['product'][:200]}\n{message}".strip()
    params = {k: (form.get(k) or "").strip() for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content",
                                                        "utm_term", "gclid", "fbclid", "nx", "nv")}
    attr = resolve_attribution(ctx.session, params)
    lead = Lead(
        landing_page_id=page.id, campaign_id=attr["campaign_id"], variant_key=attr["variant_key"],
        platform=attr["platform"], attribution=attr["attribution"], full_name=name, organisation=organisation,
        email=email, phone=(form.get("phone") or "").strip()[:60] or None, country=page.country,
        product_category=page.product_category, quantity=quantity, message=message, consent=True,
        status="new", visitor_hash=visitor,
    )
    ctx.session.add(lead)
    ctx.session.flush()
    record_event(ctx.session, page, "enquiry", params, visitor)
    ctx.tasks.create_task(agent="inbound_lead", objective_id=None, task_input={"lead_id": lead.id},
                          priority=97, idempotency_key=stable_key("inbound_lead", lead.id))
    return lead


def sync_lead_status(session, opportunity: Opportunity) -> None:
    """Keep the lead's status in step with its deal (the optimiser learns from it)."""
    lead = session.scalar(select(Lead).where(Lead.opportunity_id == opportunity.id))
    if lead is None or lead.status == "spam":
        return
    stage = opportunity.stage
    if stage == OpportunityStage.WON.value:
        lead.status = "won"
    elif stage in (OpportunityStage.LOST.value, OpportunityStage.STALE.value):
        lead.status = "lost"
    elif stage == OpportunityStage.NEGOTIATION.value and lead.status in ("new", "acknowledged"):
        lead.status = "qualified"
    session.flush()


ACK_TEMPLATE = """Dear {name},

Thank you for your enquiry about {topic}{qty}. We have received it and will reply {promise} with a written quotation or any questions we have.

If it helps, reply to this e-mail with the exact specification, delivery location and timing.

Regards,
{sender}"""


class InboundLeadAgent(BaseAgent):
    name = "inbound_lead"
    task_type = "inbound_lead"
    tier = ModelTier.BULK

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        from app.execution.send import send_email
        from app.notify import dashboard_link, notify

        lead = ctx.session.get(Lead, task_input["lead_id"])
        if lead is None or lead.status == "spam":
            return self.ok(output={"skipped": True})
        if lead.opportunity_id:
            return self.ok(output={"already_processed": True})

        domain = lead.email.split("@", 1)[1]
        free_mail = any(domain.endswith(d) for d in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com"))
        company, _ = ctx.memory.upsert_company(
            name=lead.organisation or lead.full_name, domain=None if free_mail else domain, country=lead.country,
            source=f"enquiry form ({lead.platform})", buying_signals=[f"asked for a quotation: {lead.message[:200]}"],
        )
        contact = ctx.memory.upsert_contact(
            company_id=company.id, full_name=lead.full_name, role="enquirer", email=lead.email, phone=lead.phone,
            confidence=0.95, source="enquiry form", verified=True,
        )
        opportunity, _ = ctx.memory.create_opportunity(company.id, lead.product_category)
        opportunity.contact_id = contact.id
        opportunity.qualification = {
            **(opportunity.qualification or {}),
            "inbound": True, "lead_id": lead.id, "order_potential_units": lead.quantity,
            "need_evidence": 0.95, "decision_maker_confidence": 0.9, "product_fit": 0.85, "timing": "now",
        }
        ctx.memory.transition(opportunity, OpportunityStage.RESPONSE, f"inbound enquiry via {lead.platform}")
        lead.opportunity_id = opportunity.id
        enquiry = Message(
            opportunity_id=opportunity.id, contact_id=contact.id, direction="inbound",
            subject=f"Website enquiry: {lead.product_category.replace('_', ' ')}",
            body=lead.message or "(no message)", status="processed", from_address=lead.email,
            dedupe_key=stable_key("enquiry", lead.id), simulated=ctx.settings.nexus_mode == "simulation",
        )
        ctx.session.add(enquiry)
        ctx.session.flush()

        # Acknowledge at once: no prices, no promises beyond the reply time.
        sender = ctx.settings.email_sender_name or ctx.settings.business_name or "NEXUS Sourcing"
        body = ACK_TEMPLATE.format(
            name=lead.full_name.split()[0], topic=lead.product_category.replace("_", " "),
            qty=f" ({lead.quantity} units)" if lead.quantity else "", promise=ctx.settings.lead_response_promise,
            sender=sender,
        )
        request = ActionRequest(
            kind=ActionKind.SEND_ACKNOWLEDGEMENT, summary=f"acknowledge enquiry from {lead.full_name}",
            payload={"contact_id": contact.id, "company_id": company.id, "to": lead.email, "personalized": True,
                     "subject": "We have your enquiry", "body": body,
                     "allowed_facts": {"numbers": [lead.quantity] if lead.quantity else []}},
            estimated_cost_usd=0.01, cost_category="email", idempotency_key=stable_key("ack", lead.id),
            opportunity_id=opportunity.id,
        )
        decision = ctx.authorize(request)
        if decision.decision == Decision.ALLOW:
            message = send_email(ctx, contact, "We have your enquiry", body, request.idempotency_key,
                                 opportunity=opportunity, fact_check={"validated": True})
            if message.status == "sent":
                lead.status = "acknowledged"
        notify(ctx, f"New enquiry: {lead.organisation} ({lead.country})",
               f"{lead.full_name}, {lead.email}\n{lead.product_category.replace('_', ' ')}"
               f"{' x ' + str(lead.quantity) if lead.quantity else ''}\nSource: {lead.platform}\n{lead.message[:400]}\n"
               f"{dashboard_link(ctx.settings, '#pipeline')}",
               dedupe_key=f"lead:{lead.id}")
        ctx.session.flush()
        next_tasks = [{"agent": "sourcing", "input": {"opportunity_id": opportunity.id}, "priority": 96}]
        if lead.platform in ("google", "meta"):
            next_tasks.append({"agent": "conversion_upload", "input": {"lead_id": lead.id, "event": "lead"}, "priority": 60})
        return self.ok(output={"opportunity_id": opportunity.id, "acknowledged": lead.status == "acknowledged"},
                       next_tasks=next_tasks, notes=[f"enquiry from {lead.organisation} turned into a deal"])


class InboundQuoteAgent(BaseAgent):
    """Draft the quotation for an enquiry; it waits for your approval (R-REPLY-01)."""

    name = "inbound_quote"
    task_type = "reply_draft"
    tier = ModelTier.REASONING

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        from app.agents.reply import draft_reply
        from app.database.models import Contact

        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None or opportunity.contact_id is None:
            return self.fail("opportunity or contact missing")
        contact = ctx.session.get(Contact, opportunity.contact_id)
        enquiry = ctx.session.scalar(
            select(Message).where(Message.opportunity_id == opportunity.id, Message.direction == "inbound")
            .order_by(Message.created_at.desc())
        )
        if enquiry is None:
            return self.fail("no enquiry message")
        category = "price_request" if (opportunity.qualification or {}).get("order_potential_units") else "information_request"
        drafted = draft_reply(self, ctx, enquiry, opportunity, contact, category)
        return self.ok(output={"drafted": drafted}, notes=["quotation drafted for your approval" if drafted else "no draft"])
