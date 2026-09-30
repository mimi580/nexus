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


def license_expiry_check(ctx: RunContext) -> dict:
    """Raise one compliance event per licence that is expiring or has expired."""
    from app.database.models import ComplianceEvent
    from app.policies.licenses import EXPIRY_WARNING_DAYS, expiring

    today = ctx.now.date()
    raised = []
    for lic in expiring(ctx.session, today, EXPIRY_WARNING_DAYS):
        expired = lic.expires_on < today
        flag = "license_expired" if expired else "license_expiring"
        marker = f"[{lic.id}]"
        existing = ctx.session.scalar(
            select(ComplianceEvent).where(
                ComplianceEvent.flag == flag,
                ComplianceEvent.resolved.is_(False),
                ComplianceEvent.detail.contains(marker),
            )
        )
        if existing is not None:
            continue
        days = (lic.expires_on - today).days
        detail = (
            f"{marker} {lic.country} licence {lic.license_number} "
            + (f"expired on {lic.expires_on}" if expired else f"expires on {lic.expires_on} ({days} days)")
        )
        ctx.memory.record_compliance(
            product_category=",".join(lic.product_categories or []),
            flag=flag,
            severity="high" if expired or days <= 14 else "medium",
            detail=detail,
        )
        ctx.audit.record(
            "license_alert", summary=detail, decision="escalate" if expired else "allow", license_id=lic.id
        )
        raised.append(lic.id)
    return {"raised": raised}


def catalogue_recheck(ctx: RunContext) -> dict:
    """Resume opportunities parked for a missing offer or price once it exists."""
    from app.agents.sourcing import AWAITING_OFFER, AWAITING_PRICE
    from app.commercial import catalogue
    from app.database.models import Company, ReviewItem

    today = ctx.now.date()
    resumed = 0
    closed_gaps: set[str] = set()
    parked = ctx.session.scalars(
        select(Opportunity).where(
            Opportunity.stage == OpportunityStage.COMMERCIAL_REVIEW.value,
            Opportunity.blocked_reason.in_((AWAITING_OFFER, AWAITING_PRICE)),
        )
    )
    for opportunity in parked:
        category = opportunity.product_category
        company = ctx.session.get(Company, opportunity.company_id)
        country = company.country if company else None
        if opportunity.blocked_reason == AWAITING_OFFER:
            if not catalogue.current_offers(ctx.session, category, today):
                continue
            closed_gaps.add(stable_key("catalogue_gap", "offer", category))
        else:
            if catalogue.price_reference(ctx.session, category, country, today) is None:
                continue
            closed_gaps.add(stable_key("catalogue_gap", "price", category, country or ""))
        _, new = ctx.tasks.create_task(
            agent="sourcing",
            objective_id=opportunity.objective_id,
            task_input={"opportunity_id": opportunity.id},
            priority=60,
            idempotency_key=stable_key("catalogue_recheck", opportunity.id, today.isoformat()),
        )
        resumed += int(new)
    for key in closed_gaps:
        item = ctx.session.scalar(
            select(ReviewItem).where(ReviewItem.review_key == key, ReviewItem.status == "pending")
        )
        if item is not None:
            item.status = "resolved"
            item.decided_at = ctx.now
            item.decided_by = "system"
            item.decision_note = "catalogue gap filled; parked opportunities resumed"
    ctx.session.flush()
    return {"resumed": resumed, "gaps_closed": len(closed_gaps)}


DEFAULT_JOBS = {
    "market_research_refresh": (refresh_market_research, 7 * 24 * 3600),
    "process_inbound": (process_inbound, 900),
    "due_follow_ups": (due_follow_ups, 3600),
    "opportunity_monitor": (monitor_opportunities, 24 * 3600),
    "daily_report": (daily_report, 24 * 3600),
    "weekly_learning": (weekly_learning, 7 * 24 * 3600),
    "monthly_budget_review": (monthly_budget_review, 30 * 24 * 3600),
    "license_expiry_check": (license_expiry_check, 24 * 3600),
    "catalogue_recheck": (catalogue_recheck, 6 * 3600),
}
