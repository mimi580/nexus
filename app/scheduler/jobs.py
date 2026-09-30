"""Recurring jobs. Each one is idempotent: it enqueues keyed tasks, never work."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.context import RunContext
from app.core.ids import stable_key
from app.core.types import OpportunityStage, TERMINAL_STAGES
from app.database.models import FollowUp, Message, Objective, Opportunity


def refresh_market_research(ctx: RunContext) -> dict:
    created = 0
    for objective in ctx.session.scalars(select(Objective).where(Objective.status == "active")):
        for category in (objective.constraints or {}).get("product_categories", []):
            bucket = ctx.now.strftime("%Y-%W")
            _, new = ctx.tasks.create_task(
                agent="market_research",
                objective_id=objective.id,
                task_input={"product_category": category},
                priority=85,
                idempotency_key=stable_key("market_research", objective.id, category, bucket),
            )
            created += int(new)
    return {"tasks_created": created}


def process_inbound(ctx: RunContext) -> dict:
    created = 0
    rows = ctx.session.scalars(
        select(Message).where(Message.direction == "inbound", Message.status == "received")
    )
    for message in rows:
        _, new = ctx.tasks.create_task(
            agent="response",
            objective_id=None,
            task_input={"message_id": message.id},
            priority=85,
            idempotency_key=stable_key("response", message.id),
        )
        created += int(new)
    return {"tasks_created": created}


def due_follow_ups(ctx: RunContext) -> dict:
    created = 0
    rows = ctx.session.scalars(
        select(FollowUp).where(FollowUp.status == "scheduled", FollowUp.due_at <= ctx.now)
    )
    for follow_up in rows:
        opportunity = ctx.session.get(Opportunity, follow_up.opportunity_id)
        if opportunity is None or opportunity.stage in {s.value for s in TERMINAL_STAGES}:
            follow_up.status = "stopped"
            follow_up.stop_reason = "opportunity closed"
            continue
        _, new = ctx.tasks.create_task(
            agent="follow_up",
            objective_id=opportunity.objective_id,
            task_input={
                "opportunity_id": opportunity.id,
                "follow_up_id": follow_up.id,
                "step": follow_up.step,
                "regulatory_checked": "missing_documentation" not in (opportunity.compliance_flags or []),
            },
            priority=70,
            idempotency_key=stable_key("follow_up", follow_up.id),
        )
        created += int(new)
    ctx.session.flush()
    return {"tasks_created": created}


def monitor_opportunities(ctx: RunContext, stale_after_days: int = 21) -> dict:
    stale = 0
    cutoff = ctx.now - timedelta(days=stale_after_days)
    rows = ctx.session.scalars(
        select(Opportunity).where(Opportunity.stage.notin_([s.value for s in TERMINAL_STAGES]))
    )
    for opportunity in rows:
        if opportunity.updated_at and opportunity.updated_at < cutoff:
            ctx.memory.transition(opportunity, OpportunityStage.STALE, "no movement within window")
            stale += 1
    return {"marked_stale": stale}


def weekly_learning(ctx: RunContext) -> dict:
    bucket = ctx.now.strftime("%Y-%W")
    _, new = ctx.tasks.create_task(
        agent="learning",
        task_input={"window_days": 7},
        priority=40,
        idempotency_key=stable_key("learning", bucket),
    )
    return {"tasks_created": int(new)}


def monthly_budget_review(ctx: RunContext) -> dict:
    snapshot = ctx.budget.snapshot()
    ctx.audit.record("budget_review", summary=f"budget review {snapshot['period']}", decision="allow", **snapshot)
    return snapshot


def daily_report(ctx: RunContext) -> dict:
    from app.agents.learning import metrics

    data = metrics(ctx, window_days=1)
    ctx.audit.record("daily_report", summary="daily activity report", decision="allow", **data)
    return data


DEFAULT_JOBS = {
    "market_research_refresh": (refresh_market_research, 7 * 24 * 3600),
    "process_inbound": (process_inbound, 900),
    "due_follow_ups": (due_follow_ups, 3600),
    "opportunity_monitor": (monitor_opportunities, 24 * 3600),
    "daily_report": (daily_report, 24 * 3600),
    "weekly_learning": (weekly_learning, 7 * 24 * 3600),
    "monthly_budget_review": (monthly_budget_review, 30 * 24 * 3600),
}
