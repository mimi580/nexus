"""Review queue, operator decisions, supplier catalogue, price book, production sourcing."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.agents.outreach import OutreachAgent
from app.agents.sourcing import AWAITING_OFFER, AWAITING_PRICE, SourcingAgent
from app.commercial import catalogue
from app.commercial import settings as commercial
from app.core.config import Settings
from app.core.context import build_context
from app.core.interfaces import ModelRequest
from app.core.types import Decision, ModelTier, OpportunityStage, ProductCategory
from app.database.models import Message, Outcome, ReviewItem, Task
from app.models.providers.base import render_user_message
from app.orchestrator.orchestrator import Orchestrator
from app.review import queue
from app.scheduler.jobs import catalogue_recheck

PHARMA = ProductCategory.PHARMA.value
LAPTOP = ProductCategory.LAPTOP.value


def _opportunity(ctx, category=PHARMA, country="Nigeria", name="Review Buyer"):
    company, _ = ctx.memory.upsert_company(name=name, domain=f"{name.lower().replace(' ', '')}.example", country=country)
    contact = ctx.memory.upsert_contact(
        company_id=company.id, full_name="Ada Obi", role="Procurement Manager",
        email=f"ada@{name.lower().replace(' ', '')}.example", confidence=0.7, source="directory",
    )
    opportunity, _ = ctx.memory.create_opportunity(company.id, category)
    opportunity.contact_id = contact.id
    opportunity.economics = {"quantity": 40, "lead_time_days": 12}
    opportunity.qualification = {"strategy": {"message_angle": "lead time", "followup_cadence_days": 4}}
    ctx.session.flush()
    return opportunity


def _run_as_task(ctx, agent, task_input):
    task, _ = ctx.tasks.create_task(agent=agent.name, objective_id=None, task_input=task_input)
    ctx.task_id = task.id
    try:
        return agent.run(ctx, task_input)
    finally:
        ctx.task_id = None


# ------------------------------------------------------------------ review queue


def test_escalated_outreach_becomes_a_review_with_the_exact_draft(ctx):
    opportunity = _opportunity(ctx)
    result = _run_as_task(ctx, OutreachAgent(), {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert result.output["escalated"] is True
    items = queue.pending(ctx.session)
    assert len(items) == 1
    item = items[0]
    assert item.kind == "outreach_approval"
    assert "R-REG-04" in item.rule_ids
    assert item.action_payload["to"] == "ada@reviewbuyer.example"
    assert item.action_payload["body"]


def test_approval_sends_the_reviewed_draft_once(ctx):
    opportunity = _opportunity(ctx)
    _run_as_task(ctx, OutreachAgent(), {"opportunity_id": opportunity.id, "regulatory_checked": True})
    item = queue.pending(ctx.session)[0]
    draft = item.action_payload["body"]

    queue.decide(ctx, item.id, "approve", note="partner distributor holds the import permit")
    assert item.status == "approved"
    task = ctx.session.scalar(select(Task).where(Task.input["approved_review_id"].as_string() == item.id))
    assert task is not None and task.agent == "outreach"

    result = OutreachAgent().run(ctx, task.input)
    assert result.output.get("sent") is True, result.output
    message = ctx.session.scalar(select(Message).where(Message.direction == "outbound"))
    assert message.body.startswith(draft)

    again = OutreachAgent().run(ctx, task.input)
    assert again.output.get("sent") is False  # duplicate rule still applies after approval


def test_approval_never_overrides_a_block(ctx):
    opportunity = _opportunity(ctx)
    _run_as_task(ctx, OutreachAgent(), {"opportunity_id": opportunity.id, "regulatory_checked": True})
    item = queue.pending(ctx.session)[0]
    queue.decide(ctx, item.id, "approve")
    contact_id = opportunity.contact_id
    ctx.memory.opt_out_contact(contact_id)
    task = ctx.session.scalar(select(Task).where(Task.input["approved_review_id"].as_string() == item.id))
    result = OutreachAgent().run(ctx, task.input)
    assert result.output.get("sent") is False
    assert result.output.get("blocked") is True


def test_rejection_closes_the_deal_and_is_never_reproposed(ctx):
    opportunity = _opportunity(ctx)
    _run_as_task(ctx, OutreachAgent(), {"opportunity_id": opportunity.id, "regulatory_checked": True})
    item = queue.pending(ctx.session)[0]
    queue.decide(ctx, item.id, "reject", note="not our market")
    assert opportunity.stage == OpportunityStage.LOST.value
    retry = OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert retry.output.get("blocked") is True
    assert any("rejected" in reason for reason in retry.output["reasons"])


def test_repeated_escalation_is_one_item_with_a_counter(ctx):
    opportunity = _opportunity(ctx)
    for _ in range(3):
        OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    items = ctx.session.scalars(select(ReviewItem)).all()
    assert len(items) == 1
    assert items[0].occurrences == 3


def test_non_send_items_resolve_rather_than_execute(ctx):
    ctx.audit.record(
        "human_handoff_required", summary="price_request reply needs a quotation", decision="escalate",
        opportunity_id=None,
    )
    item = queue.pending(ctx.session)[0]
    assert item.kind == "commercial_handoff"
    queue.decide(ctx, item.id, "approve")
    assert item.status == "resolved"


def test_decisions_are_validated(ctx):
    ctx.audit.record("compliance_escalation", summary="check this", decision="escalate")
    item = queue.pending(ctx.session)[0]
    with pytest.raises(queue.ReviewError):
        queue.decide(ctx, item.id, "maybe")
    queue.decide(ctx, item.id, "resolve")
    with pytest.raises(queue.ReviewError, match="already"):
        queue.decide(ctx, item.id, "resolve")


def test_operator_reported_outcome_feeds_learning(ctx):
    opportunity = _opportunity(ctx, category=LAPTOP, country="Kenya")
    queue.record_outcome(ctx, opportunity.id, "won", revenue_usd=12000, margin_usd=2100, note="PO received")
    assert opportunity.stage == OpportunityStage.WON.value
    outcome = ctx.session.scalar(select(Outcome).where(Outcome.opportunity_id == opportunity.id))
    assert outcome.result == "won" and outcome.revenue_usd == 12000
    with pytest.raises(queue.ReviewError):
        queue.record_outcome(ctx, opportunity.id, "draw")


# ------------------------------------------------------------------ catalogue


def _offer(session, **overrides):
    fields = dict(
        supplier_name="Dubai IT Liquidators FZE", supplier_country="UAE", product_category=LAPTOP,
        product_name="Dell Latitude 5420 i5/16GB/256GB", condition="grade A refurbished",
        quantity_available=200, unit_cost_low_usd=195, unit_cost_high_usd=205, incoterm="FOB Dubai",
        shipping_cost_usd=900, lead_time_days=10, payment_terms="T/T in advance",
        documents="commercial invoice|certificate of origin", valid_until="2026-12-31",
        source="quote DIL-2026-114 (email 2026-09-20)",
    )
    fields.update(overrides)
    return catalogue.add_offer(session, **fields)


def _price(session, **overrides):
    fields = dict(
        product_category=LAPTOP, country="Kenya", unit_price_low_usd=310, unit_price_high_usd=350,
        basis="three Nairobi reseller listings, grade A 5420", source="field survey 2026-09-18",
        valid_until="2026-12-31",
    )
    fields.update(overrides)
    return catalogue.add_price_reference(session, **fields)


def test_offers_must_be_traceable(session):
    with pytest.raises(catalogue.CatalogueError, match="source"):
        _offer(session, source="")
    with pytest.raises(catalogue.CatalogueError, match="below"):
        _offer(session, unit_cost_low_usd=300, unit_cost_high_usd=200)
    with pytest.raises(catalogue.CatalogueError, match="category"):
        _offer(session, product_category="drones")


def test_expired_offers_are_ignored(session):
    _offer(session, valid_until="2026-09-01")
    assert catalogue.current_offers(session, LAPTOP, date(2026, 9, 17)) == []
    _offer(session)
    offers = catalogue.current_offers(session, LAPTOP, date(2026, 9, 17))
    assert len(offers) == 1 and offers[0]["source"].startswith("quote DIL")


def test_country_price_beats_general_price(session):
    _price(session, country=None, unit_price_low_usd=280, unit_price_high_usd=300, basis="general")
    _price(session)
    on = date(2026, 9, 17)
    assert catalogue.price_reference(session, LAPTOP, "Kenya", on).unit_price_low_usd == 310
    assert catalogue.price_reference(session, LAPTOP, "Uganda", on).unit_price_low_usd == 280


def test_csv_import_is_all_or_nothing(session):
    header = catalogue.template("offers")
    good = "Acme Refurb,UAE,refurbished_laptop,HP EliteBook 840 G6,grade A,50,,150,160,FOB,400,7,T/T,invoice,,2026-12-31,quote Q1,0.7,\n"
    bad = "Acme Refurb,UAE,refurbished_laptop,HP EliteBook 840 G6,grade A,50,,abc,160,FOB,400,7,T/T,invoice,,2026-12-31,quote Q2,0.7,\n"
    result = catalogue.import_csv(session, header + good + bad, "offers")
    assert result["imported"] == 0 and "row 3" in result["errors"][0]
    assert catalogue.list_offers(session) == []
    result = catalogue.import_csv(session, header + good, "offers")
    assert result["imported"] == 1


def test_commercial_settings_validation(session):
    updated = commercial.update(session, {"min_margin_pct": {LAPTOP: 18}, "duties_taxes_pct": {"Kenya": 0.16}})
    assert updated["min_margin_pct"][LAPTOP] == 18
    assert commercial.get(session)["duties_taxes_pct"]["Kenya"] == 0.16
    with pytest.raises(commercial.CommercialSettingsError):
        commercial.update(session, {"duties_taxes_pct": {"Kenya": 16}})
    with pytest.raises(commercial.CommercialSettingsError):
        commercial.update(session, {"favourite_colour": "blue"})


# ------------------------------------------------------------------ production sourcing


@pytest.fixture
def prod_ctx(session, clock):
    settings = Settings(
        _env_file=None, nexus_env="production", nexus_mode="production",
        database_url="sqlite+pysqlite:///:memory:", anthropic_api_key=None,
    )
    return build_context(session, settings, clock=clock)


def test_production_router_never_uses_mock_models(prod_ctx):
    assert prod_ctx.router.registrations == []


def test_production_run_is_refused_until_ready(prod_ctx):
    summary = Orchestrator(prod_ctx).run()
    assert summary["stop_reason"] == "not_production_ready"
    assert any("ANTHROPIC_API_KEY" in item for item in summary["missing"])


def test_production_sourcing_parks_until_the_catalogue_has_an_offer(prod_ctx):
    opportunity = _opportunity(prod_ctx, category=LAPTOP, country="Kenya", name="Nairobi School")
    opportunity.qualification = {"order_potential_units": 40}
    result = SourcingAgent().run(prod_ctx, {"opportunity_id": opportunity.id})
    assert result.output["parked"] == AWAITING_OFFER
    assert opportunity.stage == OpportunityStage.COMMERCIAL_REVIEW.value
    gap = queue.pending(prod_ctx.session)[0]
    assert gap.kind == "catalogue"

    _offer(prod_ctx.session)
    assert catalogue_recheck(prod_ctx)["resumed"] == 1
    result = SourcingAgent().run(prod_ctx, {"opportunity_id": opportunity.id})
    assert result.output["parked"] == AWAITING_PRICE  # offer found, price book still empty

    _price(prod_ctx.session)
    catalogue_recheck(prod_ctx)
    result = SourcingAgent().run(prod_ctx, {"opportunity_id": opportunity.id})
    assert result.output["pursued"] is True, result.output
    economics = opportunity.economics
    assert economics["offer_id"]
    assert economics["supplier_source"].startswith("quote DIL")
    assert "duties_taxes_pct" in economics["unknowns"]  # no operator duty rate for Kenya yet
    assert prod_ctx.session.scalar(
        select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "pending")
    ) == 0  # both catalogue gaps closed by the recheck job


def test_production_margin_floor_comes_from_operator_settings(prod_ctx):
    commercial.update(prod_ctx.session, {"min_margin_pct": {LAPTOP: 60}})
    _offer(prod_ctx.session)
    _price(prod_ctx.session)
    opportunity = _opportunity(prod_ctx, category=LAPTOP, country="Kenya", name="Margin School")
    opportunity.qualification = {"order_potential_units": 40}
    result = SourcingAgent().run(prod_ctx, {"opportunity_id": opportunity.id})
    assert result.output["pursued"] is False
    assert opportunity.stage == OpportunityStage.LOST.value


# ------------------------------------------------------------------ providers


def test_live_providers_receive_the_context():
    request = ModelRequest(
        task_type="outreach_copy", prompt="Write the email.", tier=ModelTier.BULK,
        context={"company_name": "Kisumu Referral Hospital", "quantity": 6},
    )
    rendered = render_user_message(request)
    assert "Write the email." in rendered
    assert "Kisumu Referral Hospital" in rendered and '"quantity": 6' in rendered
