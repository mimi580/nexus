"""Supplier research, scoring, RFQs, follow-ups, quote intake; buyer listings and LinkedIn enrichment."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.agents.prospecting import DecisionMakerAgent, ProspectDiscoveryAgent
from app.agents.response import ResponseAgent
from app.agents.sourcing import SourcingAgent
from app.agents.suppliers import (
    SupplierProfileAgent,
    SupplierResearchAgent,
    SupplierRFQAgent,
    handle_supplier_reply,
    score_supplier,
)
from app.commercial import catalogue
from app.core.config import Settings
from app.core.interfaces import ActionRequest
from app.core.types import ActionKind, Decision, ProductCategory
from app.database.models import Company, Contact, Message, ReviewItem, SupplierOffer, SupplierRFQ, Task
from app.review import queue
from app.scheduler.jobs import supplier_followups
from app.tools.fetch import StaticFetcher
from app.tools.search import PlaceHit, SearchHit, StaticSearch
from tests.test_research import _ctx as grounded_ctx

LAPTOP = ProductCategory.LAPTOP.value
PHARMA = ProductCategory.PHARMA.value


def _qualified_supplier(ctx, category=LAPTOP):
    research = SupplierResearchAgent().run(ctx, {"product_category": category})
    assert research.ok and research.output["created"] >= 1
    company_id = research.next_tasks[0]["input"]["company_id"]
    profile = SupplierProfileAgent().run(ctx, {"company_id": company_id, "product_category": category})
    return ctx.session.get(Company, company_id), profile


# ------------------------------------------------------------------ research and profile (simulation)


def test_simulated_suppliers_are_found_profiled_and_qualified(ctx):
    company, profile = _qualified_supplier(ctx)
    assert company.kind == "supplier"
    assert company.status == "qualified", profile.output
    assert company.score >= 0.5
    contact = ctx.session.scalar(select(Contact).where(Contact.company_id == company.id))
    assert contact.email.startswith("sales@")
    assert profile.next_tasks[0]["agent"] == "supplier_rfq"


def test_supplier_and_buyer_with_the_same_name_stay_separate(ctx):
    ctx.memory.upsert_company(name="Dual Co", domain="dual.example", country="Kenya")
    supplier, created = ctx.memory.upsert_company(name="Dual Co", domain="dual.example", country="Kenya", kind="supplier")
    assert created and supplier.kind == "supplier" and supplier.status == "lead"


def test_supplier_scoring_flags_risks():
    good, flags = score_supplier(LAPTOP, {"products": ["refurbished Dell Latitude"], "certifications": ["R2v3"],
                                          "export_evidence": ["ships to Africa"], "address": "Dubai"}, "sales@good.example", "good.example")
    assert good > 0.9 and flags == []
    risky, flags = score_supplier(LAPTOP, {"products": [], "certifications": []}, "cheapphones@gmail.com", None)
    assert risky < 0.3
    assert "free webmail address only" in flags and "no own website" in flags


# ------------------------------------------------------------------ RFQs


def test_rfq_goes_to_the_supplier_with_demand_quantities(ctx):
    company, _ = _qualified_supplier(ctx)
    result = SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    assert result.output.get("sent") is True, result.output
    rfq = ctx.session.scalar(select(SupplierRFQ))
    assert rfq.status == "sent" and rfq.items[0]["quantity"] > 0
    message = ctx.session.get(Message, rfq.first_message_id)
    assert message.opportunity_id is None and "request for prices, not an order" in message.body
    assert company.status == "rfq_sent"
    again = SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    assert again.output.get("sent") is False  # one RFQ per supplier per line per month


def test_pharma_rfq_to_a_supplier_abroad_is_not_blocked_by_buyer_licence_rules(ctx):
    company, _ = _qualified_supplier(ctx, PHARMA)
    assert company.country not in {"Kenya"}  # e.g. India
    result = SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": PHARMA})
    assert result.output.get("sent") is True, result.output


def test_approval_mode_and_caps_apply_to_rfqs(ctx):
    company, _ = _qualified_supplier(ctx)
    contact = ctx.session.scalar(select(Contact).where(Contact.company_id == company.id))
    request = ActionRequest(
        kind=ActionKind.SEND_SUPPLIER_RFQ, summary="rfq",
        payload={"contact_id": contact.id, "company_id": company.id, "subject": "RFQ", "personalized": True,
                 "body": "Please quote for laptops. Reply unsubscribe to opt out.", "allowed_facts": {"numbers": []}},
        idempotency_key="rfq-test",
    )
    ctx.policy.settings = Settings(_env_file=None, require_outreach_approval=True)
    assert ctx.policy.evaluate(request, now=ctx.now).decision == Decision.ESCALATE
    ctx.policy.settings = Settings(_env_file=None, supplier_rfq_daily_limit=0)
    blocked = ctx.policy.evaluate(request, now=ctx.now)
    assert blocked.decision == Decision.BLOCK and "R-RATE-03" in blocked.rule_ids


def test_blocked_supplier_is_never_contacted(ctx):
    company, _ = _qualified_supplier(ctx)
    company.opted_out = True
    ctx.session.flush()
    result = SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    assert result.output.get("sent") is False and result.output.get("blocked")


def test_follow_ups_then_unresponsive(ctx, clock):
    company, _ = _qualified_supplier(ctx)
    SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    rfq = ctx.session.scalar(select(SupplierRFQ))
    for step in range(1, ctx.settings.supplier_rfq_max_followups + 1):
        clock.advance(days=ctx.settings.supplier_rfq_followup_days + 1)
        assert supplier_followups(ctx)["tasks_created"] == 1
        task = ctx.session.scalar(select(Task).where(Task.agent == "supplier_rfq", Task.input["step"].as_integer() == step))
        assert SupplierRFQAgent().run(ctx, task.input).output.get("sent") is True
    clock.advance(days=ctx.settings.supplier_rfq_followup_days + 1)
    assert supplier_followups(ctx)["closed"] == 1
    assert rfq.status == "no_response" and company.status == "unresponsive"


# ------------------------------------------------------------------ replies and quotes


def _supplier_reply(ctx, company, text):
    contact = ctx.session.scalar(select(Contact).where(Contact.company_id == company.id))
    message = Message(contact_id=contact.id, direction="inbound", subject="Re: Request for quotation", body=text,
                      status="received", dedupe_key=f"sup-{len(text)}", from_address=contact.email)
    ctx.session.add(message)
    ctx.session.flush()
    return message


QUOTE = ("Thank you for your request for quotation. We can offer refurbished laptop at USD 212.5 per unit FOB, "
         "MOQ 10 units, 160 units available, lead time 10 days. Payment: 30% deposit, balance before shipment. "
         "Documents: commercial invoice, packing list. Offer valid for 14 days.")


def test_quote_becomes_draft_offers_that_only_count_after_activation(ctx):
    company, _ = _qualified_supplier(ctx)
    SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    message = _supplier_reply(ctx, company, QUOTE)
    result = ResponseAgent().run(ctx, {"message_id": message.id})
    assert result.output == {"category": "quote", "supplier": True}
    offer = ctx.session.scalar(select(SupplierOffer))
    assert offer.status == "pending_review" and offer.active is False
    assert offer.unit_cost_low_usd == 212.5 and offer.moq == 10 and offer.quantity_available == 160
    assert catalogue.current_offers(ctx.session, LAPTOP, ctx.now.date()) == []  # not usable yet

    item = ctx.session.scalar(select(ReviewItem))
    assert item.kind == "supplier_quote" and item.action_payload["offer_ids"] == [offer.id]
    queue.decide(ctx, item.id, "approve", note="checked against e-mail")
    assert offer.active is True and offer.status == "active"
    assert len(catalogue.current_offers(ctx.session, LAPTOP, ctx.now.date())) == 1
    assert ctx.session.scalar(select(SupplierRFQ)).status == "quoted"


def test_prices_not_in_the_email_are_never_recorded(ctx, monkeypatch):
    company, _ = _qualified_supplier(ctx)
    SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    message = _supplier_reply(ctx, company, QUOTE)
    from app.agents import base

    real_ask = base.BaseAgent.ask

    def fake_ask(self, ctx_, prompt, context, **kw):
        if kw.get("task_type") == "supplier_quote_extraction":
            return {"lines": [{"product_name": "laptop", "unit_price": 150, "currency": "USD"}]}, 0.0
        return real_ask(self, ctx_, prompt, context, **kw)

    monkeypatch.setattr(base.BaseAgent, "ask", fake_ask)
    ResponseAgent().run(ctx, {"message_id": message.id})
    assert ctx.session.scalar(select(SupplierOffer)) is None


def test_non_usd_quotes_cannot_be_activated_blindly(ctx):
    company, _ = _qualified_supplier(ctx)
    SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    message = _supplier_reply(ctx, company, QUOTE)
    ResponseAgent().run(ctx, {"message_id": message.id})
    offer = ctx.session.scalar(select(SupplierOffer))
    offer.currency = "EUR"
    item = ctx.session.scalar(select(ReviewItem))
    with pytest.raises(queue.ReviewError, match="EUR"):
        queue.decide(ctx, item.id, "approve")
    assert item.status == "pending" and offer.active is False


def test_declines_and_questions(ctx):
    company, _ = _qualified_supplier(ctx)
    SupplierRFQAgent().run(ctx, {"company_id": company.id, "product_category": LAPTOP})
    ResponseAgent().run(ctx, {"message_id": _supplier_reply(ctx, company, "Could you share your target price?").id})
    assert ctx.session.scalar(select(ReviewItem)).kind == "supplier_reply"
    ResponseAgent().run(ctx, {"message_id": _supplier_reply(ctx, company, "Thank you, but we are not able to supply this line at the moment.").id})
    assert company.status == "declined"


# ------------------------------------------------------------------ grounded supplier research


def test_grounded_supplier_research_accepts_only_named_suppliers(session, clock):
    hit = SearchHit("Gulf ITAD LLC - wholesale refurbished laptops, Dubai", "https://gulfitad.example/",
                    "Gulf ITAD LLC exports refurbished Dell Latitude laptops in bulk.", 1)
    search = StaticSearch({"laptops": [hit]})
    ctx, provider = grounded_ctx(session, clock, {}, search=search)

    class Late(dict):
        def get(self, key, default=None):
            if key != "supplier_discovery":
                return default
            from app.database.models import SourceDocument

            doc = ctx.session.scalar(select(SourceDocument).where(SourceDocument.url == hit.url))
            return {"suppliers": [
                {"name": "Gulf ITAD LLC", "source_id": doc.id, "website_url": "https://gulfitad.example/",
                 "country": "United Arab Emirates", "evidence_quote": "exports refurbished Dell Latitude laptops"},
                {"name": "Phantom Traders", "source_id": doc.id},
            ]}

    provider.replies = Late()
    result = SupplierResearchAgent().run(ctx, {"product_category": LAPTOP, "regions": ["United Arab Emirates"]})
    assert result.output["created"] == 1 and result.output["rejected"] == 1
    supplier = ctx.session.scalar(select(Company).where(Company.kind == "supplier"))
    assert supplier.name == "Gulf ITAD LLC" and supplier.domain == "gulfitad.example"


# ------------------------------------------------------------------ buyers: listings, LinkedIn, gap trigger


def test_business_listings_add_buyers_with_their_own_websites(session, clock):
    places = {"hospital in Kenya": [
        PlaceHit("Aga Khan Hospital Kisumu", "Otieno Oyoo St, Kisumu", "https://www.akhk.example/", "", "Hospital", 1),
        PlaceHit("No Website Clinic", "Nairobi", "", "", "Clinic", 2),
        PlaceHit("Some Clinic", "Nairobi", "https://facebook.com/someclinic", "", "Clinic", 3),
    ]}
    search = StaticSearch({}, places_results=places)
    ctx, _ = grounded_ctx(session, clock, {"prospect_discovery": {"prospects": []}}, search=search)
    clock.now = clock.now.replace(month=1, day=2)  # week 0 -> first listing query
    result = ProspectDiscoveryAgent().run(ctx, {"product_category": ProductCategory.MEDICAL.value, "countries": ["Kenya"]})
    assert result.output["from_listings"] == 1
    company = ctx.session.scalar(select(Company).where(Company.name == "Aga Khan Hospital Kisumu"))
    assert company.domain == "akhk.example" and company.country == "Kenya"


def test_linkedin_result_names_the_person_behind_a_published_mailbox(session, clock):
    fetcher = StaticFetcher({"https://ksh.example/contact": ("Contact", "Write to info@ksh.example")})
    search = StaticSearch({"site:linkedin.com/in": [
        SearchHit("Mary Wanjiku - Head of Procurement - Kisumu Specialist Hospital | LinkedIn",
                  "https://ke.linkedin.com/in/mary-wanjiku", "Kisumu Specialist Hospital. Procurement", 1),
        SearchHit("John Doe - Head of Procurement - Other Hospital | LinkedIn",
                  "https://ke.linkedin.com/in/john-doe", "Other Hospital", 2),
    ]})
    ctx, _ = grounded_ctx(session, clock, {"decision_maker_discovery": {"email": "nope@x.example"}}, search=search, fetcher=fetcher)
    company, _ = ctx.memory.upsert_company(name="Kisumu Specialist Hospital", domain="ksh.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, ProductCategory.MEDICAL.value)
    result = DecisionMakerAgent().run(ctx, {"opportunity_id": opportunity.id})
    contact = ctx.session.get(Contact, result.output["contact_id"])
    assert contact.email == "info@ksh.example"
    assert contact.full_name == "Mary Wanjiku"
    assert contact.linkedin_url == "https://ke.linkedin.com/in/mary-wanjiku"
    assert "via published mailbox" in contact.role


def test_catalogue_gap_triggers_supplier_research(session, clock):
    ctx, _ = grounded_ctx(session, clock, {})
    company, _ = ctx.memory.upsert_company(name="School", domain="school.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, LAPTOP)
    opportunity.qualification = {"order_potential_units": 30}
    result = SourcingAgent().run(ctx, {"opportunity_id": opportunity.id})
    assert result.next_tasks[0]["agent"] == "supplier_research"


def test_supplier_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import reset_settings_cache
    from app.database.session import reset_engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/sup.sqlite3")
    monkeypatch.setenv("NEXUS_MODE", "simulation")
    reset_settings_cache()
    reset_engine()
    from app.api.main import app

    try:
        with TestClient(app) as client:
            assert client.post("/api/suppliers/research", json={"product_category": LAPTOP}).json()["queued"] is True
            client.post("/api/run")
            rows = client.get("/api/suppliers").json()
            assert rows and rows[0]["name"]
            blocked = client.post(f"/api/suppliers/{rows[0]['id']}/status", json={"status": "blocked"})
            assert blocked.json()["status"] == "blocked"
            assert client.post(f"/api/suppliers/{rows[0]['id']}/status", json={"status": "vip"}).status_code == 422
    finally:
        reset_engine()
        reset_settings_cache()
