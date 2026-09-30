"""Human review queue.

Every escalation — a policy decision that needs a human, a buyer asking for a
quotation, a compliance problem, a licence alert — becomes one ReviewItem. The
operator approves, rejects or resolves it. Approval never bypasses a block:
an approved outreach is re-run through the full policy engine, and only the
escalation that caused the review is waived.

Items are keyed, so the same escalation raised twice is one item with a
counter, and a rejected action is never re-proposed (policy rule R-REV-01).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.ids import stable_key
from app.core.types import OpportunityStage, utcnow
from app.database.models import FollowUp, Opportunity, Outcome, ReviewItem, Task

SEND_KINDS = {"send_outreach", "send_followup"}
DECISIONS = {"approve", "reject", "resolve"}

KIND_BY_EVENT = {
    "human_handoff_required": "commercial_handoff",
    "compliance_escalation": "compliance",
    "compliance_block": "compliance",
    "license_alert": "license",
    "catalogue_gap": "catalogue",
    "research_gap": "research",
}


class ReviewError(ValueError):
    pass


EXECUTABLE_KINDS = {"outreach_approval", "reply_approval"}


def classify(event_type: str, action_kind: str | None) -> str:
    if action_kind == "send_reply":
        return "reply_approval"
    if action_kind in SEND_KINDS:
        return "outreach_approval"
    if action_kind in {"financial_commitment", "legal_commitment"}:
        return "commitment"
    return KIND_BY_EVENT.get(event_type, "other")


def open_item(
    session: Session,
    *,
    event_type: str,
    title: str,
    reasons: list[str] | None = None,
    rule_ids: list[str] | None = None,
    review_key: str | None = None,
    opportunity_id: str | None = None,
    objective_id: str | None = None,
    task_id: str | None = None,
    action_kind: str | None = None,
    action_payload: dict[str, Any] | None = None,
) -> tuple[ReviewItem, bool]:
    key = review_key or stable_key(event_type, opportunity_id or "", title[:200])
    existing = session.scalar(select(ReviewItem).where(ReviewItem.review_key == key))
    if existing is not None:
        existing.occurrences = (existing.occurrences or 1) + 1
        if existing.status == "pending":
            existing.reasons = list(dict.fromkeys([*(existing.reasons or []), *(reasons or [])]))
            existing.rule_ids = list(dict.fromkeys([*(existing.rule_ids or []), *(rule_ids or [])]))
            if action_payload:
                existing.action_payload = action_payload
        session.flush()
        return existing, False
    item = ReviewItem(
        review_key=key,
        kind=classify(event_type, action_kind),
        status="pending",
        event_type=event_type,
        title=title[:400],
        reasons=list(reasons or []),
        rule_ids=list(rule_ids or []),
        opportunity_id=opportunity_id,
        objective_id=objective_id,
        task_id=task_id,
        action_kind=action_kind,
        action_payload=action_payload or {},
    )
    session.add(item)
    session.flush()
    return item, True


def approved_for(session: Session, review_id: str | None, review_key: str | None) -> ReviewItem | None:
    """The approved review that authorises exactly this action, if any."""
    if not review_id or not review_key:
        return None
    item = session.get(ReviewItem, review_id)
    if item is None or item.status != "approved" or item.review_key != review_key:
        return None
    return item


def rejected(session: Session, review_key: str | None) -> ReviewItem | None:
    if not review_key:
        return None
    return session.scalar(
        select(ReviewItem).where(ReviewItem.review_key == review_key, ReviewItem.status == "rejected")
    )


def pending(session: Session, limit: int = 100) -> list[ReviewItem]:
    return list(
        session.scalars(
            select(ReviewItem)
            .where(ReviewItem.status == "pending")
            .order_by(ReviewItem.created_at)
            .limit(limit)
        )
    )


def counts(session: Session) -> dict[str, int]:
    return {
        status: count
        for status, count in session.execute(
            select(ReviewItem.status, func.count()).group_by(ReviewItem.status)
        ).all()
    }


def decide(
    ctx: Any,
    review_id: str,
    decision: str,
    note: str = "",
    actor: str = "operator",
    *,
    subject: str | None = None,
    body: str | None = None,
) -> ReviewItem:
    """Apply an operator decision. ctx is a RunContext.

    For a proposed message, subject/body let the operator edit the draft; the
    edited text is what gets sent (and it still passes every block rule).
    """
    session: Session = ctx.session
    if decision not in DECISIONS:
        raise ReviewError(f"decision must be one of {sorted(DECISIONS)}")
    item = session.get(ReviewItem, review_id)
    if item is None:
        raise ReviewError(f"no review item {review_id}")
    if item.status != "pending":
        raise ReviewError(f"review item already {item.status}")
    if decision == "approve" and item.kind not in EXECUTABLE_KINDS:
        # Only a proposed send can be executed on approval. Everything else is
        # handled by the operator outside NEXUS and closed with 'resolve'.
        decision = "resolve"

    if decision == "approve" and (subject is not None or body is not None):
        if body is not None and not body.strip():
            raise ReviewError("the message body cannot be empty")
        payload = dict(item.action_payload or {})
        if subject is not None and subject.strip() != payload.get("subject", ""):
            payload["subject"] = subject.strip()[:300]
            payload["operator_edited"] = True
        if body is not None and body.strip() != (payload.get("body") or "").strip():
            payload["body"] = body.strip()
            payload["operator_edited"] = True
        item.action_payload = payload
    item.status = {"approve": "approved", "reject": "rejected", "resolve": "resolved"}[decision]
    item.decided_at = ctx.now
    item.decided_by = actor
    item.decision_note = note[:4000] if note else None
    opportunity = session.get(Opportunity, item.opportunity_id) if item.opportunity_id else None

    follow_on: dict[str, Any] = {}
    if item.status == "approved":
        follow_on = _requeue_send(ctx, item)
    elif item.status == "rejected" and opportunity is not None:
        reason = f"operator rejected: {note or item.kind}"
        ctx.memory.transition(opportunity, OpportunityStage.LOST, reason, actor=actor)
        session.add(Outcome(opportunity_id=opportunity.id, result="lost", attributes={"reason": "operator_rejected", "review_id": item.id}))
        for row in session.scalars(
            select(FollowUp).where(FollowUp.opportunity_id == opportunity.id, FollowUp.status == "scheduled")
        ):
            row.status = "stopped"
            row.stop_reason = "operator rejected"

    ctx.audit.record(
        "review_decided",
        summary=f"{item.kind} {item.status}: {item.title}",
        actor=actor,
        decision="allow",
        review_id=item.id,
        note=note,
        **follow_on,
    )
    session.flush()
    return item


def _requeue_send(ctx: Any, item: ReviewItem) -> dict[str, Any]:
    session: Session = ctx.session
    if item.action_kind == "send_reply":
        task, created = ctx.tasks.create_task(
            agent="reply",
            objective_id=item.objective_id,
            task_input={"review_id": item.id},
            priority=92,
            idempotency_key=stable_key("review_approval", item.id),
        )
        session.flush()
        return {"requeued": created, "task_id": task.id}
    original = session.get(Task, item.task_id) if item.task_id else None
    if original is None:
        return {"requeued": False, "why": "originating task not found"}
    task_input = dict(original.input or {})
    task_input["approved_review_id"] = item.id
    if original.agent == "follow_up" and task_input.get("follow_up_id"):
        follow_up = session.get(FollowUp, task_input["follow_up_id"])
        if follow_up is not None and follow_up.status in {"skipped", "scheduled"}:
            follow_up.status = "scheduled"
            follow_up.stop_reason = None
    task, created = ctx.tasks.create_task(
        agent=original.agent,
        objective_id=original.objective_id,
        task_input=task_input,
        priority=90,
        idempotency_key=stable_key("review_approval", item.id),
    )
    session.flush()
    return {"requeued": created, "task_id": task.id}


def as_dict(item: ReviewItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "status": item.status,
        "title": item.title,
        "reasons": item.reasons,
        "rule_ids": item.rule_ids,
        "opportunity_id": item.opportunity_id,
        "action_kind": item.action_kind,
        "action": item.action_payload,
        "occurrences": item.occurrences,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "decided_by": item.decided_by,
        "decision_note": item.decision_note,
    }


def record_outcome(
    ctx: Any,
    opportunity_id: str,
    result: str,
    *,
    revenue_usd: float | None = None,
    margin_usd: float | None = None,
    note: str = "",
    actor: str = "operator",
) -> Outcome:
    """Operator-reported deal result: the learning loop's ground truth."""
    if result not in {"won", "lost"}:
        raise ReviewError("result must be 'won' or 'lost'")
    opportunity = ctx.session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise ReviewError(f"no opportunity {opportunity_id}")
    stage = OpportunityStage.WON if result == "won" else OpportunityStage.LOST
    ctx.memory.transition(opportunity, stage, f"operator reported {result}: {note}"[:300], actor=actor)
    outcome = Outcome(
        opportunity_id=opportunity.id,
        result=result,
        revenue_usd=revenue_usd,
        margin_usd=margin_usd,
        attributes={"reported_by": actor, "note": note, "reported_at": utcnow().isoformat()},
    )
    ctx.session.add(outcome)
    for row in ctx.session.scalars(
        select(FollowUp).where(FollowUp.opportunity_id == opportunity.id, FollowUp.status == "scheduled")
    ):
        row.status = "stopped"
        row.stop_reason = f"deal {result}"
    ctx.audit.record(
        "outcome_reported", summary=f"{opportunity_id} {result}", actor=actor, decision="allow",
        revenue_usd=revenue_usd, margin_usd=margin_usd,
    )
    ctx.session.flush()
    return outcome
