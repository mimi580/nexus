import pytest
from sqlalchemy import func, select

from app.agents.market_research import MarketResearchAgent, score_market
from app.agents.prospecting import DecisionMakerAgent, ProspectDiscoveryAgent
from app.agents.qualification import OpportunityScoringAgent, QualificationAgent
from app.agents.sourcing import SourcingAgent
from app.core.types import OpportunityStage, ProductCategory
from app.database.models import Company, ComplianceEvent, Contact, Opportunity


def test_market_scoring_penalises_risk_and_competition():
    safe = score_market(dict(demand=0.8, procurement=0.8, purchasing_power=0.6, supplier_availability=0.8,
                            logistics=0.8, competition=0.2, payment_risk=0.2, regulation=0.8))
    risky = score_market(dict(demand=0.8, procurement=0.8, purchasing_power=0.6, supplier_availability=0.8,
                              logistics=0.8, competition=0.9, payment_risk=0.9, regulation=0.8))
    assert safe > risky


def test_market_research_ranks_and_spawns_prospecting(ctx):
    result = MarketResearchAgent().run(ctx, {"product_category": ProductCategory.LAPTOP.value})
    assert result.ok
    ranked = result.output["ranked_markets"]
    assert ranked == sorted(ranked, key=lambda m: m["score"], reverse=True)
    assert result.next_tasks[0]["agent"] == "prospect_discovery"


def test_country_ranking_is_not_hard_coded(ctx):
    """The same fixtures scored with different weights must reorder."""
    default = MarketResearchAgent().run(ctx, {"product_category": ProductCategory.IPHONE.value})
    reweighted = MarketResearchAgent().run(
        ctx,
        {
            "product_category": ProductCategory.IPHONE.value,
            "weights": {"purchasing_power": 1.0, "payment_risk": -0.5},
        },
    )
    assert [m["country"] for m in default.output["ranked_markets"]] != [
        m["country"] for m in reweighted.output["ranked_markets"]
    ]


def test_prospect_discovery_deduplicates(ctx):
    task = {"product_category": ProductCategory.MEDICAL.value, "countries": ["Kenya"], "limit": 5}
    first = ProspectDiscoveryAgent().run(ctx, task)
    second = ProspectDiscoveryAgent().run(ctx, task)
    assert first.output["created"] > 0
    assert second.output["created"] == 0
    assert second.output["duplicates"] == first.output["created"]
    assert second.next_tasks == []


def test_decision_maker_refuses_contacts_without_a_source(ctx, monkeypatch):
    company, _ = ctx.memory.upsert_company(name="No Source Ltd", domain="nosource.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, ProductCategory.LAPTOP.value)
    monkeypatch.setattr(
        DecisionMakerAgent,
        "ask",
        lambda self, ctx, prompt, context, **kw: (
            {"contacts": [{"full_name": "Invented Person", "role": "CEO", "confidence": 0.9}]},
            0.0,
        ),
    )
    result = DecisionMakerAgent().run(ctx, {"opportunity_id": opportunity.id})
    assert result.ok
    assert ctx.session.scalar(select(func.count()).select_from(Contact)) == 0
    assert opportunity.stage == OpportunityStage.STALE.value


def test_scoring_is_explainable_and_reproducible(ctx):
    company, _ = ctx.memory.upsert_company(name="Scored Co", domain="scored.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, ProductCategory.LAPTOP.value)
    QualificationAgent().run(ctx, {"opportunity_id": opportunity.id})
    first = OpportunityScoringAgent().run(ctx, {"opportunity_id": opportunity.id})
    breakdown = opportunity.score_breakdown
    assert set(breakdown) == {"components", "weights", "weights_version", "contributions", "threshold"}
    assert pytest.approx(opportunity.score, rel=1e-9) == first.output["score"]


def test_low_scores_are_dropped_not_pursued(ctx):
    company, _ = ctx.memory.upsert_company(name="Weak Co", domain="weak.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, ProductCategory.LAPTOP.value)
    opportunity.qualification = {
        "product_fit": 0.05, "need_evidence": 0.05, "order_potential_units": 1,
        "timing": "unknown", "decision_maker_confidence": 0.05, "supplier_feasibility": 0.1,
        "regulatory_risk": 0.9, "payment_risk": 0.9,
    }
    ctx.session.flush()
    result = OpportunityScoringAgent(threshold=0.6).run(ctx, {"opportunity_id": opportunity.id})
    assert result.output["pursued"] is False
    assert opportunity.stage == OpportunityStage.LOST.value


def test_regulated_line_without_documents_is_escalated(ctx, monkeypatch):
    company, _ = ctx.memory.upsert_company(name="Pharma Buyer", domain="pharmabuyer.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, ProductCategory.PHARMA.value)
    opportunity.qualification = {"order_potential_units": 500}
    ctx.session.flush()
    monkeypatch.setattr(
        SourcingAgent,
        "ask",
        lambda self, ctx, prompt, context, **kw: (
            {
                "offers": [
                    {
                        "supplier_name": "Undocumented Supplier",
                        "country": "India",
                        "unit_cost_usd": 10.0,
                        "quantity_available": 10000,
                        "reliability": 0.6,
                        "documents": [],
                        "shipping_cost_usd": 500,
                        "source": "test",
                    }
                ]
            },
            0.0,
        ),
    )
    result = SourcingAgent().run(ctx, {"opportunity_id": opportunity.id})
    assert result.output["blocked"] is True
    assert opportunity.stage == OpportunityStage.COMMERCIAL_REVIEW.value
    assert ctx.session.scalar(
        select(func.count()).select_from(ComplianceEvent).where(ComplianceEvent.flag == "missing_documentation")
    ) == 1


def test_thin_margin_opportunities_are_dropped(ctx, monkeypatch):
    company, _ = ctx.memory.upsert_company(name="Thin Margin Co", domain="thin.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, ProductCategory.LAPTOP.value)
    opportunity.qualification = {"order_potential_units": 40}
    ctx.session.flush()
    monkeypatch.setattr(
        SourcingAgent,
        "ask",
        lambda self, ctx, prompt, context, **kw: (
            {
                "offers": [
                    {
                        "supplier_name": "Expensive Supplier",
                        "country": "UAE",
                        "unit_cost_usd": 330.0,
                        "unit_cost_high_usd": 340.0,
                        "quantity_available": 500,
                        "reliability": 0.8,
                        "documents": ["invoice"],
                        "shipping_cost_usd": 800,
                        "source": "test",
                    }
                ]
            },
            0.0,
        ),
    )
    result = SourcingAgent().run(ctx, {"opportunity_id": opportunity.id})
    assert result.output["pursued"] is False
    assert opportunity.stage == OpportunityStage.LOST.value
