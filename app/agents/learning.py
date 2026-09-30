"""Outcome measurement and the weekly learning pass.

Strategy changes are data, never code: a new Strategy row is activated only
when it measurably predicts better, and is rolled back automatically if it
does worse live (app/learning/loop.py).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.interfaces import AgentResult
from app.core.types import ModelTier, OpportunityStage
from app.database.models import Company, Interaction, Message, Opportunity


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
    """Weekly: refit the scoring model (or roll it back), and publish what NEXUS has learned.

    The day-to-day learning happens at each decision (see app/learning/loop.py):
    this agent does the slower, versioned part and writes the snapshot the
    dashboard's Learning tab shows.
    """

    name = "learning"
    task_type = "learning_review"
    tier = ModelTier.REASONING
    complexity = 0.75

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        from app.learning import loop

        window = int(task_input.get("window_days", 30))
        data = metrics(ctx, window)
        rollback = loop.check_rollback(ctx)
        calibration = loop.calibrate_scoring(ctx) if rollback.get("rollback") != "rolled back" else {
            "decision": "skipped this week: a rollback just happened"}
        snap = loop.snapshot(ctx)
        snap.update(metrics=data, calibration=calibration, rollback=rollback)
        observations, cost = [], 0.0
        try:
            review, cost = self.ask(
                ctx,
                "Summarise, for the operator, what these results and learned preferences say, in at most five "
                "plain observations. Do not recommend changes the data does not support. "
                "Return {'observations':[]}.",
                {"metrics": data, "calibration": calibration, "email": snap["email"], "ads": snap["ads"]},
            )
            observations = [str(o) for o in (review.get("observations") or [])][:5]
        except Exception as exc:  # noqa: BLE001 - the narrative is optional; the learning is not
            observations = [f"narrative unavailable: {exc}"]
        snap["observations"] = observations
        loop.store_snapshot(ctx, snap)
        ctx.audit.record(
            "learning_review", summary=f"scoring: {calibration.get('decision')}; rollback check: {rollback.get('rollback')}",
            decision="allow", task_id=ctx.task_id, metrics=data, calibration=calibration, rollback=rollback,
        )
        return self.ok(output={"metrics": data, "calibration": calibration, "rollback": rollback},
                       cost_usd=cost, notes=[str(calibration.get("decision")), str(rollback.get("rollback"))])
