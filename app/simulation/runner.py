"""End-to-end simulation: the whole platform with no credentials and no sends."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.context import build_context
from app.core.types import ProductCategory
from app.database.models import (
    AuditEvent,
    ComplianceEvent,
    Contact,
    Evidence,
    Message,
    Opportunity,
    Task,
)
from app.database.session import create_all, reset_engine, session_scope
from app.orchestrator.loop import LoopLimits
from app.orchestrator.orchestrator import Orchestrator
from app.scheduler.scheduler import Scheduler

DEFAULT_CATEGORIES = [
    ProductCategory.LAPTOP.value,
    ProductCategory.IPHONE.value,
    ProductCategory.MEDICAL.value,
    ProductCategory.PHARMA.value,
    ProductCategory.SERVER_IT.value,
]


class VirtualClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 17, 8, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> None:
        self.now += timedelta(**kwargs)


def run_simulation(
    database_url: str | None = None,
    days: int = 10,
    categories: list[str] | None = None,
    settings: Settings | None = None,
    verbose: bool = False,
) -> dict:
    settings = settings or get_settings()
    if database_url:
        reset_engine()
    create_all(database_url)
    clock = VirtualClock()
    report: dict[str, Any] = {"days": days, "cycles": []}

    with session_scope() as session:
        ctx = build_context(session, settings, clock=clock)
        orchestrator = Orchestrator(
            ctx, limits=LoopLimits(max_iterations=400, max_cost_usd=25.0, max_duration_seconds=600)
        )
        scheduler = Scheduler(ctx)

        objective = orchestrator.create_objective(
            title="Build qualified B2B pipeline across the initial product lines",
            product_categories=categories or DEFAULT_CATEGORIES,
            description="Simulation objective: discover, qualify, source, contact and learn.",
        )
        report["objective_id"] = objective.id

        from app.policies.licenses import add_license, list_licenses
        from app.simulation.fixtures import LICENSES

        existing = {(lic.country, lic.license_number) for lic in list_licenses(session, True)}
        for fixture in LICENSES:
            if (fixture["country"], fixture["license_number"]) not in existing:
                add_license(session, **fixture)

        for day in range(days):
            loop = orchestrator.run(objective.id)
            jobs = scheduler.run_due()
            report["cycles"].append(
                {"day": day, "date": clock.now.date().isoformat(), "loop": loop, "jobs": jobs}
            )
            if verbose:
                print(f"day {day}: {loop['iterations']} iterations, stop={loop['stop_reason']}")
            if ctx.budget.hard_stopped():
                report["halted"] = "budget_hard_stop"
                break
            clock.advance(days=1)

        # Final learning pass over everything that happened.
        ctx.tasks.create_task(
            agent="learning", objective_id=objective.id, task_input={"window_days": days + 1}, priority=95
        )
        orchestrator.run(objective.id)

        report["pipeline"] = {
            stage: count
            for stage, count in session.execute(
                select(Opportunity.stage, func.count()).group_by(Opportunity.stage)
            ).all()
        }
        report["messages"] = {
            f"{direction}:{status}": count
            for direction, status, count in session.execute(
                select(Message.direction, Message.status, func.count()).group_by(
                    Message.direction, Message.status
                )
            ).all()
        }
        report["tasks"] = {
            status: count
            for status, count in session.execute(
                select(Task.status, func.count()).group_by(Task.status)
            ).all()
        }
        report["evidence_records"] = session.scalar(select(func.count()).select_from(Evidence))
        report["compliance_events"] = session.scalar(select(func.count()).select_from(ComplianceEvent))
        report["opt_outs"] = session.scalar(
            select(func.count()).select_from(Contact).where(Contact.opted_out.is_(True))
        )
        report["escalations"] = session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.decision == "escalate")
        )
        report["blocked_actions"] = session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.decision == "block")
        )
        report["budget"] = ctx.budget.snapshot()
        report["license_escalations"] = sum(
            1
            for payload in session.scalars(
                select(AuditEvent.payload).where(AuditEvent.event_type == "policy_decision")
            )
            if "R-REG-04" in (payload or {}).get("rule_ids", [])
        )

        from app.agents.learning import metrics

        report["metrics"] = metrics(ctx, window_days=days + 2)

    return report
