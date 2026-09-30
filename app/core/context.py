"""Runtime context passed to every agent. One session, one policy gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.audit.logger import DbAuditLogger
from app.budget.controller import BudgetController
from app.core.config import Settings, get_settings
from app.core.errors import EscalationRequired, PolicyViolation
from app.core.interfaces import ActionRequest, PolicyResult
from app.core.types import Decision, utcnow
from app.memory.store import MemoryStore, TaskStore
from app.models.router import ModelRouter
from app.policies.engine import PolicyEngine


PREVIEW_FIELDS = (
    "to", "subject", "body", "country", "product_category", "step", "amount_usd", "contact_id", "company_id",
    "in_reply_to_message_id", "their_message", "pricing", "allowed_facts", "regulatory_checked",
)


def _preview(request: ActionRequest) -> dict[str, Any]:
    """What the operator needs to see to decide: the exact draft, recipient, market."""
    return {k: request.payload[k] for k in PREVIEW_FIELDS if k in request.payload}


@dataclass
class RunContext:
    session: Session
    settings: Settings
    memory: MemoryStore
    tasks: TaskStore
    router: ModelRouter
    policy: PolicyEngine
    budget: BudgetController
    audit: DbAuditLogger
    email: Any = None
    research: Any = None
    inbox: Any = None
    clock: Any = field(default=utcnow)
    objective_id: str | None = None
    task_id: str | None = None

    @property
    def now(self) -> datetime:
        return self.clock()

    # ------------------------------------------------------------ gatekeeping
    def authorize(self, request: ActionRequest, *, raise_on_block: bool = False) -> PolicyResult:
        result = self.policy.evaluate(request, now=self.now)
        self.audit.record(
            "policy_decision",
            summary=f"{request.kind.value}: {request.summary}",
            decision=result.decision.value,
            objective_id=self.objective_id or request.objective_id,
            task_id=self.task_id,
            rule_ids=result.rule_ids,
            reasons=result.reasons,
            risk=result.risk.value,
            review_key=request.idempotency_key,
            action_kind=request.kind.value,
            opportunity_id=request.opportunity_id,
            action_preview=_preview(request) if result.decision == Decision.ESCALATE else None,
        )
        if raise_on_block and result.decision == Decision.BLOCK:
            raise PolicyViolation("; ".join(result.reasons) or "blocked", rules=result.rule_ids)
        if raise_on_block and result.decision == Decision.ESCALATE:
            raise EscalationRequired("; ".join(result.reasons) or "escalated", rules=result.rule_ids)
        return result


def build_context(
    session: Session,
    settings: Settings | None = None,
    *,
    email: Any = None,
    clock: Any = utcnow,
    router: ModelRouter | None = None,
    research: Any = None,
    inbox: Any = None,
) -> RunContext:
    from app.execution.email import build_email_provider
    from app.models.router import build_default_router
    from app.execution.inbound import build_inbox
    from app.tools.research import build_research

    settings = settings or get_settings()
    budget = BudgetController(session, settings)
    policy = PolicyEngine(session, budget, settings)
    return RunContext(
        session=session,
        settings=settings,
        memory=MemoryStore(session),
        tasks=TaskStore(session),
        router=router or build_default_router(session, budget, settings),
        policy=policy,
        budget=budget,
        audit=DbAuditLogger(session),
        email=email or build_email_provider(settings),
        research=research if research is not None else build_research(session, settings, budget, clock),
        inbox=inbox if inbox is not None else build_inbox(settings),
        clock=clock,
    )
