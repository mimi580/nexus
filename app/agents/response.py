"""Inbound response classification and routing."""

from __future__ import annotations

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.interfaces import AgentResult
from app.core.types import (
    EvidenceKind,
    ModelTier,
    OpportunityStage,
    ResponseCategory,
    STOP_FOLLOWUP_CATEGORIES,
)
from app.database.models import Company, Contact, FollowUp, Interaction, Message, Opportunity, Outcome

POSITIVE = {
    ResponseCategory.INTERESTED,
    ResponseCategory.INFORMATION_REQUEST,
    ResponseCategory.PRICE_REQUEST,
    ResponseCategory.RFQ,
    ResponseCategory.NEGOTIATION,
}


class ResponseAgent(BaseAgent):
    name = "response"
    task_type = "response_classification"
    tier = ModelTier.BULK
    complexity = 0.35

    def _stop_followups(self, ctx: RunContext, opportunity_id: str, reason: str) -> None:
        rows = ctx.session.scalars(
            select(FollowUp).where(FollowUp.opportunity_id == opportunity_id, FollowUp.status == "scheduled")
        )
        for row in rows:
            row.status = "stopped"
            row.stop_reason = reason[:160]
        ctx.session.flush()

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        message = ctx.session.get(Message, task_input["message_id"])
        if message is None or message.direction != "inbound":
            return self.fail("inbound message not found")
        if message.status == "processed":
            return self.ok(output={"already_processed": True})
        opportunity = ctx.session.get(Opportunity, message.opportunity_id) if message.opportunity_id else None
        contact = ctx.session.get(Contact, message.contact_id) if message.contact_id else None
        if contact is not None:
            company = ctx.session.get(Company, contact.company_id)
            if company is not None and company.kind == "supplier":
                from app.agents.suppliers import handle_supplier_reply

                return handle_supplier_reply(self, ctx, message, contact, company)

        prompt = (
            "Classify this reply into exactly one category from: interested, information_request, "
            "price_request, rfq, negotiation, not_interested, wrong_contact, unsubscribe, complaint, "
            "suspicious, regulatory_issue, other. Return {'category','confidence','notes'}."
        )
        data, cost = self.ask(ctx, prompt, {"reply_text": message.body})
        try:
            category = ResponseCategory(data.get("category", "other"))
        except ValueError:
            category = ResponseCategory.OTHER

        message.status = "processed"
        ctx.session.add(
            Interaction(
                opportunity_id=message.opportunity_id,
                company_id=opportunity.company_id if opportunity else None,
                contact_id=message.contact_id,
                direction="inbound",
                category=category.value,
                summary=message.body[:400],
                payload={"confidence": data.get("confidence")},
            )
        )
        self.persist_evidence(
            ctx,
            [
                self.evidence(
                    f"reply classified as {category.value}",
                    source=f"message:{message.id}",
                    kind=EvidenceKind.VERIFIED_FACT,
                    confidence=float(data.get("confidence", 0.5)),
                    source_type="inbound_message",
                    subject_type="opportunity",
                    subject_id=message.opportunity_id,
                )
            ],
        )
        ctx.session.flush()

        next_tasks: list[dict] = []
        notes: list[str] = []

        if opportunity is not None:
            self._stop_followups(ctx, opportunity.id, f"reply received: {category.value}")

        if category in (ResponseCategory.UNSUBSCRIBE, ResponseCategory.COMPLAINT):
            if contact is not None:
                ctx.memory.opt_out_contact(contact.id, reason=category.value)
            if opportunity is not None:
                ctx.memory.transition(opportunity, OpportunityStage.LOST, category.value)
                ctx.session.add(
                    Outcome(opportunity_id=opportunity.id, result="lost", attributes={"reason": category.value})
                )
            ctx.audit.record(
                "opt_out_recorded",
                summary=f"{category.value} from {contact.email if contact else 'unknown'}",
                decision="allow",
                task_id=ctx.task_id,
            )
            notes.append("contact suppressed; no further messages will be sent")

        elif category == ResponseCategory.NOT_INTERESTED:
            if opportunity is not None:
                ctx.memory.transition(opportunity, OpportunityStage.LOST, "not interested")
                ctx.session.add(
                    Outcome(opportunity_id=opportunity.id, result="lost", attributes={"reason": "not_interested"})
                )

        elif category == ResponseCategory.WRONG_CONTACT:
            if opportunity is not None:
                ctx.memory.transition(opportunity, OpportunityStage.RESEARCHED, "wrong contact; re-routing")
                next_tasks.append(
                    {
                        "agent": "decision_maker_discovery",
                        "input": {"opportunity_id": opportunity.id, "exclude_contact_id": message.contact_id},
                        "priority": 62,
                    }
                )

        elif category == ResponseCategory.REGULATORY_ISSUE:
            if opportunity is not None:
                ctx.memory.record_compliance(
                    opportunity_id=opportunity.id,
                    product_category=opportunity.product_category,
                    flag="counterparty_regulatory_requirement",
                    severity="high",
                    detail=message.body[:500],
                )
                opportunity.compliance_flags = list(
                    {*(opportunity.compliance_flags or []), "counterparty_regulatory_requirement"}
                )
                ctx.memory.transition(
                    opportunity, OpportunityStage.COMMERCIAL_REVIEW, "counterparty raised a regulatory requirement"
                )
            ctx.audit.record(
                "compliance_escalation",
                summary="counterparty raised a regulatory requirement",
                decision="escalate",
                task_id=ctx.task_id,
            )
            notes.append("escalated for compliance review")

        elif category in POSITIVE:
            if opportunity is not None:
                stage = (
                    OpportunityStage.NEGOTIATION
                    if category in (ResponseCategory.RFQ, ResponseCategory.NEGOTIATION)
                    else OpportunityStage.RESPONSE
                )
                ctx.memory.transition(opportunity, stage, f"positive reply: {category.value}")
            # A quotation is a commercial commitment: NEXUS drafts, a human approves.
            drafted = False
            if opportunity is not None and contact is not None:
                from app.agents.reply import draft_reply

                drafted = draft_reply(self, ctx, message, opportunity, contact, category.value)
            ctx.audit.record(
                "human_handoff_required",
                summary=f"{category.value} reply needs a quotation or commercial answer",
                decision="allow" if drafted else "escalate",
                task_id=ctx.task_id,
                opportunity_id=message.opportunity_id,
                drafted=drafted,
            )
            notes.append("draft reply awaiting your approval" if drafted else "queued for human commercial response")

        return self.ok(
            output={"category": category.value, "confidence": data.get("confidence")},
            cost_usd=cost,
            next_tasks=next_tasks,
            notes=notes or [f"classified as {category.value}"],
        )
