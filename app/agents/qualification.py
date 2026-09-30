"""Qualification and explainable opportunity scoring."""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.interfaces import AgentResult
from app.core.types import EvidenceKind, ModelTier, OpportunityStage, REGULATED_CATEGORIES
from app.database.models import Company, Contact, MarketAssessment, Opportunity
from app.commercial import settings as commercial
from sqlalchemy import select

SCORE_WEIGHTS = {
    "buyer_probability": 0.22,
    "product_fit": 0.16,
    "order_size": 0.12,
    "margin": 0.14,
    "supplier_availability": 0.10,
    "market_attractiveness": 0.10,
    "payment_risk": -0.08,
    "regulatory_risk": -0.05,
    "time_to_close": 0.03,
}

TIMING_SCORES = {"this quarter": 0.9, "next quarter": 0.55, "unknown": 0.3}


class QualificationAgent(BaseAgent):
    name = "qualification"
    task_type = "qualification"
    tier = ModelTier.BULK
    complexity = 0.55

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        company = ctx.session.get(Company, opportunity.company_id)
        contact = ctx.session.get(Contact, opportunity.contact_id) if opportunity.contact_id else None
        assert company is not None

        typical = commercial.typical_order_qty(
            ctx.session, opportunity.product_category, ctx.settings.nexus_mode == "simulation"
        )
        prompt = (
            f"Qualify {company.name} for {opportunity.product_category}. Return "
            "{'product_fit','need_evidence','order_potential_units','timing','decision_maker_confidence',"
            "'budget_indicator','supplier_feasibility','regulatory_risk','payment_risk','reasons':[]}."
        )
        data, cost = self.ask(
            ctx,
            prompt,
            {
                "company_name": company.name,
                "segment": company.segment,
                "country": company.country,
                "product_category": opportunity.product_category,
                "buying_signals": company.buying_signals,
                "typical_qty": typical,
            },
        )
        data["decision_maker_confidence"] = float(
            contact.confidence if contact else data.get("decision_maker_confidence", 0.2)
        )
        opportunity.qualification = data
        ctx.session.flush()

        evidence = [
            self.evidence(
                f"qualification summary for {company.name}: fit={data.get('product_fit')}",
                source="qualification_agent",
                kind=EvidenceKind.INFERENCE,
                confidence=0.5,
                subject_type="opportunity",
                subject_id=opportunity.id,
            )
        ]
        self.persist_evidence(ctx, evidence)
        ctx.memory.transition(opportunity, OpportunityStage.QUALIFIED, "qualified")
        return self.ok(
            output=data,
            cost_usd=cost,
            evidence=evidence,
            next_tasks=[
                {"agent": "opportunity_scoring", "input": {"opportunity_id": opportunity.id}, "priority": 60}
            ],
        )


class OpportunityScoringAgent(BaseAgent):
    """Deterministic and explainable: no model call, every component is stored."""

    name = "opportunity_scoring"
    task_type = "opportunity_scoring"

    def __init__(self, threshold: float = 0.45) -> None:
        self.threshold = threshold

    def _market_score(self, ctx: RunContext, country: str | None, category: str) -> float:
        if not country:
            return 0.4
        row = ctx.session.scalar(
            select(MarketAssessment)
            .where(
                MarketAssessment.country == country,
                MarketAssessment.product_category == category,
            )
            .order_by(MarketAssessment.assessed_at.desc())
        )
        return float(row.score) if row else 0.4

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        company = ctx.session.get(Company, opportunity.company_id)
        qual = opportunity.qualification or {}
        typical_qty = commercial.typical_order_qty(
            ctx.session, opportunity.product_category, ctx.settings.nexus_mode == "simulation"
        )

        components = {
            "buyer_probability": round(
                (float(qual.get("need_evidence", 0.3)) * 0.6)
                + (float(qual.get("decision_maker_confidence", 0.2)) * 0.4),
                4,
            ),
            "product_fit": float(qual.get("product_fit", 0.4)),
            "order_size": round(min(1.0, float(qual.get("order_potential_units", 0)) / (typical_qty * 1.5)), 4),
            "margin": float(task_input.get("expected_margin_score", 0.5)),
            "supplier_availability": float(qual.get("supplier_feasibility", 0.5)),
            "market_attractiveness": self._market_score(
                ctx, company.country if company else None, opportunity.product_category
            ),
            "payment_risk": float(qual.get("payment_risk", 0.4)),
            "regulatory_risk": float(qual.get("regulatory_risk", 0.3)),
            "time_to_close": TIMING_SCORES.get(str(qual.get("timing", "unknown")), 0.3),
        }

        total = 0.0
        contributions = {}
        for key, weight in SCORE_WEIGHTS.items():
            value = components[key]
            contribution = value * weight if weight > 0 else (1.0 - value) * abs(weight)
            contributions[key] = round(contribution, 5)
            total += contribution
        denominator = sum(abs(w) for w in SCORE_WEIGHTS.values())
        score = round(max(0.0, min(1.0, total / denominator)), 4)

        opportunity.score = score
        opportunity.score_breakdown = {
            "components": components,
            "weights": SCORE_WEIGHTS,
            "contributions": contributions,
            "threshold": self.threshold,
        }
        ctx.memory.transition(opportunity, OpportunityStage.SCORED, f"score={score}")

        if score < self.threshold:
            ctx.memory.transition(
                opportunity, OpportunityStage.LOST, f"score {score} below threshold {self.threshold}"
            )
            return self.ok(
                output={"score": score, "pursued": False},
                notes=[f"dropped: score {score} < {self.threshold}"],
            )

        if opportunity.product_category in {c.value for c in REGULATED_CATEGORIES}:
            ctx.memory.record_compliance(
                opportunity_id=opportunity.id,
                product_category=opportunity.product_category,
                flag="regulated_category",
                severity="medium",
                detail="regulated product line: documentation and regulator requirements must be verified",
            )
            flags = list(opportunity.compliance_flags or [])
            if "regulated_category" not in flags:
                flags.append("regulated_category")
            opportunity.compliance_flags = flags
            ctx.session.flush()

        return self.ok(
            output={"score": score, "pursued": True, "breakdown": opportunity.score_breakdown},
            next_tasks=[
                {"agent": "sourcing", "input": {"opportunity_id": opportunity.id}, "priority": 58}
            ],
        )
