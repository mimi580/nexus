"""Outcome measurement and versioned strategy experiments.

Strategy changes are data, never code: a new Strategy row is proposed,
activated, measured and can be rolled back.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.interfaces import AgentResult
from app.core.types import ModelTier, OpportunityStage
from app.database.models import (
    Company,
    Experiment,
    Interaction,
    Message,
    Opportunity,
    Outcome,
    Strategy,
)


POSITIVE_CATEGORIES = ("interested", "information_request", "price_request", "rfq", "negotiation")


def metrics(ctx: RunContext, window_days: int = 30) -> dict:
    since = ctx.now - timedelta(days=window_days)
    sent = list(
        ctx.session.scalars(
            select(Message).where(
                Message.direction == "outbound", Message.status == "sent", Message.created_at >= since,
                Message.opportunity_id.is_not(None),  # buyer-side only; supplier RFQs are tracked separately
            )
        )
    )
    inbound = list(
        ctx.session.scalars(
            select(Message).where(
                Message.direction == "inbound",
                Message.status.in_(("received", "processed")),  # not bounces, auto-replies or strangers
                Message.opportunity_id.is_not(None),
                Message.created_at >= since,
            )
        )
    )
    opportunities = list(ctx.session.scalars(select(Opportunity)))
    qualified = [o for o in opportunities if o.score and o.score > 0]
    won = [o for o in opportunities if o.stage == OpportunityStage.WON.value]
    positive = list(
        ctx.session.scalars(
            select(Interaction).where(
                Interaction.direction == "inbound",
                Interaction.category.in_(POSITIVE_CATEGORIES),
                Interaction.created_at >= since,
            )
        )
    )
    spend = ctx.budget.snapshot()
    total_cost = spend["committed_usd"]
    pipeline = round(sum(o.estimated_margin_usd or 0.0 for o in opportunities if o.stage not in {
        OpportunityStage.LOST.value, OpportunityStage.STALE.value}), 2)

    by_country: dict[str, dict] = {}
    for opp in opportunities:
        company = ctx.session.get(Company, opp.company_id)
        key = (company.country if company else None) or "unknown"
        bucket = by_country.setdefault(key, {"opportunities": 0, "avg_score": 0.0, "_scores": []})
        bucket["opportunities"] += 1
        bucket["_scores"].append(opp.score or 0.0)
    for bucket in by_country.values():
        scores = bucket.pop("_scores")
        bucket["avg_score"] = round(sum(scores) / len(scores), 4) if scores else 0.0

    by_category: dict[str, dict] = {}
    for opp in opportunities:
        bucket = by_category.setdefault(opp.product_category, {"opportunities": 0, "won": 0, "avg_score": 0.0, "_s": []})
        bucket["opportunities"] += 1
        bucket["_s"].append(opp.score or 0.0)
        if opp.stage == OpportunityStage.WON.value:
            bucket["won"] += 1
    for bucket in by_category.values():
        scores = bucket.pop("_s")
        bucket["avg_score"] = round(sum(scores) / len(scores), 4) if scores else 0.0

    return {
        "window_days": window_days,
        "messages_sent": len(sent),
        "replies": len(inbound),
        "response_rate": round(len(inbound) / len(sent), 4) if sent else 0.0,
        "qualified_response_rate": round(len(positive) / len(sent), 4) if sent else 0.0,
        "opportunities": len(opportunities),
        "qualified_opportunities": len(qualified),
        "won": len(won),
        "conversion_rate": round(len(won) / len(opportunities), 4) if opportunities else 0.0,
        "pipeline_margin_usd": pipeline,
        "cost_usd": total_cost,
        "cost_per_qualified_lead_usd": round(total_cost / len(qualified), 4) if qualified else None,
        "by_country": by_country,
        "by_product": by_category,
    }


class LearningAgent(BaseAgent):
    name = "learning"
    task_type = "learning_review"
    tier = ModelTier.REASONING
    complexity = 0.75

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        window = int(task_input.get("window_days", 30))
        data = metrics(ctx, window)
        _, cost = self.ask(
            ctx,
            "Review these commercial results and suggest bounded strategy changes. "
            "Return {'observations':[],'recommended_changes':[],'confidence'}.",
            {"metrics": data},
        )

        proposals: list[dict] = []
        # Deterministic guardrails decide what actually changes.
        if data["messages_sent"] >= 10 and data["qualified_response_rate"] < 0.05:
            proposals.append({"name": "scoring", "change": {"threshold": 0.55}, "why": "low qualified response rate"})
        weakest = sorted(data["by_product"].items(), key=lambda kv: kv[1]["avg_score"])
        if len(weakest) > 1 and weakest[0][1]["avg_score"] < 0.35:
            proposals.append(
                {
                    "name": "product_focus",
                    "change": {"deprioritise": weakest[0][0]},
                    "why": "persistently low opportunity scores",
                }
            )

        created = []
        for proposal in proposals:
            current = ctx.session.scalar(
                select(Strategy)
                .where(Strategy.name == proposal["name"], Strategy.active.is_(True))
                .order_by(Strategy.version.desc())
            )
            next_version = (current.version + 1) if current else 1
            strategy = Strategy(
                name=proposal["name"],
                version=next_version,
                parameters=proposal["change"],
                active=False,
                rationale=proposal["why"],
            )
            ctx.session.add(strategy)
            ctx.session.flush()
            ctx.session.add(
                Experiment(
                    strategy_name=proposal["name"],
                    control_version=current.version if current else 0,
                    variant_version=next_version,
                    status="proposed",
                    metric="qualified_response_rate",
                    result={"baseline": data["qualified_response_rate"]},
                )
            )
            created.append({"strategy": proposal["name"], "version": next_version})
        ctx.session.flush()

        ctx.audit.record(
            "learning_review",
            summary=f"metrics reviewed over {window} days",
            decision="allow",
            task_id=ctx.task_id,
            metrics=data,
            proposals=created,
        )
        return self.ok(
            output={"metrics": data, "proposals": created},
            cost_usd=cost,
            notes=[f"{len(created)} strategy proposals recorded (inactive until activated)"],
        )
