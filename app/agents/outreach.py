"""Outreach and follow-up execution.

The model writes the copy. Deterministic code decides whether it may be sent,
what facts it may assert, who may receive it and how often.
"""

from __future__ import annotations

from datetime import timedelta

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.ids import stable_key
from app.core.interfaces import ActionRequest, AgentResult
from app.core.types import ActionKind, Decision, ModelTier, OpportunityStage, REGULATED_CATEGORIES, utcnow
from app.policies.licenses import coverage
from app.database.models import Company, Contact, FollowUp, Message, Interaction, Opportunity

SEND_COST_USD = 0.01  # per message, charged to the email category


def license_statement(ctx: RunContext, opportunity: Opportunity, company: Company) -> str | None:
    """A licence sentence the copy may use, only when the register covers this deal."""
    if opportunity.product_category not in {c.value for c in REGULATED_CATEGORIES}:
        return None
    result = coverage(ctx.session, company.country, opportunity.product_category, ctx.now.date())
    if not result.covered or result.license is None:
        return None
    lic = result.license
    return f"{lic.holder_name} is a licensed {' and '.join(lic.license_types)} in {lic.country}"


def allowed_facts_for(
    opportunity: Opportunity, quantity: int | None, license_verified: bool = False
) -> dict:
    economics = opportunity.economics or {}
    numbers = []
    if quantity:
        numbers.append(quantity)
    if economics.get("lead_time_days"):
        numbers.append(economics["lead_time_days"])
    return {
        "numbers": numbers,
        "regulatory_verified": "regulatory_verified" in (opportunity.compliance_flags or []),
        "relationship_verified": False,
        "license_verified": license_verified,
    }


class OutreachAgent(BaseAgent):
    name = "outreach"
    task_type = "outreach_copy"
    tier = ModelTier.BULK
    complexity = 0.5
    step = 0

    def _compose(self, ctx: RunContext, opportunity: Opportunity, company: Company, contact: Contact, step: int):
        strategy = (opportunity.qualification or {}).get("strategy", {})
        quantity = (opportunity.economics or {}).get("quantity")
        prompt = (
            "Write a short, accurate, personalised business email. Assert only facts supplied in context. "
            "Include an opt-out line. Return {'subject','body','personalized'}."
        )
        return self.ask(
            ctx,
            prompt,
            {
                "company_name": company.name,
                "contact_name": contact.full_name.split()[0],
                "product_category": opportunity.product_category,
                "buying_signals": company.buying_signals,
                "message_angle": strategy.get("message_angle"),
                "quantity": quantity,
                "sender_name": ctx.settings.email_sender_name or "NEXUS Sourcing",
                "step": step,
                "license_statement": license_statement(ctx, opportunity, company),
            },
        )

    def _deliver(
        self,
        ctx: RunContext,
        opportunity: Opportunity,
        contact: Contact,
        subject: str,
        body: str,
        dedupe_key: str,
        step: int,
        fact_check: dict,
    ) -> Message:
        message = Message(
            opportunity_id=opportunity.id,
            contact_id=contact.id,
            direction="outbound",
            subject=subject,
            body=body,
            sequence_step=step,
            status="queued",
            dedupe_key=dedupe_key,
            fact_check=fact_check,
        )
        ctx.session.add(message)
        ctx.session.flush()

        reservation = ctx.budget.reserve("email", SEND_COST_USD, f"send:{message.id}")
        result = ctx.email.send(
            to=contact.email or "",
            subject=subject,
            body=body,
            context={"product_category": opportunity.product_category, "step": step},
        )
        message.provider = result.provider
        message.simulated = result.simulated
        if not result.ok:
            message.status = "failed"
            message.error = result.error
            ctx.budget.release(reservation)
            return message

        ctx.budget.commit(reservation, max(result.cost_usd, SEND_COST_USD))
        message.status = "sent"
        message.sent_at = ctx.now
        message.provider_message_id = result.provider_message_id
        ctx.session.add(
            Interaction(
                opportunity_id=opportunity.id,
                company_id=opportunity.company_id,
                contact_id=contact.id,
                direction="outbound",
                summary=subject,
                payload={"step": step},
            )
        )
        ctx.session.flush()

        if result.simulated_reply:
            inbound = Message(
                opportunity_id=opportunity.id,
                contact_id=contact.id,
                direction="inbound",
                subject=f"Re: {subject}",
                body=result.simulated_reply,
                status="received",
                dedupe_key=stable_key(dedupe_key, "reply"),
                simulated=True,
                provider=result.provider,
            )
            ctx.session.add(inbound)
            ctx.session.flush()
            ctx.tasks.create_task(
                agent="response",
                objective_id=opportunity.objective_id,
                task_input={"message_id": inbound.id},
                priority=80,
                idempotency_key=stable_key("response", inbound.id),
                scheduled_for=ctx.now + timedelta(hours=result.reply_delay_hours or 6),
            )
        return message

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        company = ctx.session.get(Company, opportunity.company_id)
        contact = ctx.session.get(Contact, opportunity.contact_id) if opportunity.contact_id else None
        if company is None or contact is None:
            return self.fail("company or contact missing")

        step = int(task_input.get("step", self.step))
        copy, cost = self._compose(ctx, opportunity, company, contact, step)
        quantity = (opportunity.economics or {}).get("quantity")
        dedupe_key = stable_key(contact.id, opportunity.id, "outreach", step)

        request = ActionRequest(
            kind=ActionKind.SEND_OUTREACH if step == 0 else ActionKind.SEND_FOLLOWUP,
            summary=f"email {contact.full_name} at {company.name}",
            payload={
                "contact_id": contact.id,
                "company_id": company.id,
                "opportunity_id": opportunity.id,
                "product_category": opportunity.product_category,
                "country": company.country,
                "subject": copy.get("subject", ""),
                "body": copy.get("body", ""),
                "personalized": bool(copy.get("personalized", True)),
                "allowed_facts": allowed_facts_for(
                    opportunity, quantity, license_verified=license_statement(ctx, opportunity, company) is not None
                ),
                "regulatory_checked": bool(task_input.get("regulatory_checked", False)),
                "step": step,
            },
            estimated_cost_usd=SEND_COST_USD,
            cost_category="email",
            idempotency_key=dedupe_key,
            objective_id=opportunity.objective_id,
            opportunity_id=opportunity.id,
        )
        decision = ctx.authorize(request)
        if decision.decision == Decision.ESCALATE:
            ctx.memory.transition(
                opportunity, OpportunityStage.COMMERCIAL_REVIEW, "; ".join(decision.reasons)
            )
            return self.ok(
                output={"sent": False, "escalated": True, "reasons": decision.reasons},
                cost_usd=cost,
                notes=["escalated to human review before contact"],
            )
        if decision.decision == Decision.BLOCK:
            return self.ok(
                output={"sent": False, "blocked": True, "reasons": decision.reasons},
                cost_usd=cost,
                notes=[f"blocked: {'; '.join(decision.reasons)}"],
            )

        message = self._deliver(
            ctx,
            opportunity,
            contact,
            copy["subject"],
            copy["body"],
            dedupe_key,
            step,
            {"validated": True},
        )
        if message.status != "sent":
            return self.fail(f"send failed: {message.error}", cost_usd=cost)

        ctx.memory.transition(opportunity, OpportunityStage.OUTREACH, "first contact sent")
        cadence = int((opportunity.qualification or {}).get("strategy", {}).get("followup_cadence_days", 4))
        follow_up = FollowUp(
            opportunity_id=opportunity.id,
            contact_id=contact.id,
            step=step + 1,
            due_at=ctx.now + timedelta(days=cadence),
        )
        ctx.session.add(follow_up)
        ctx.session.flush()
        return self.ok(
            output={"sent": True, "message_id": message.id},
            cost_usd=cost,
            notes=[f"outreach sent to {contact.email}"],
        )


class FollowUpAgent(OutreachAgent):
    """Same send path, bounded by the follow-up policy rules."""

    name = "follow_up"

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        follow_up = ctx.session.get(FollowUp, task_input["follow_up_id"]) if task_input.get("follow_up_id") else None
        if follow_up is not None and follow_up.status != "scheduled":
            return self.ok(output={"sent": False}, notes=[f"follow-up already {follow_up.status}"])

        step = int(task_input.get("step") or (follow_up.step if follow_up else 1))
        result = super().run(
            ctx,
            {
                "opportunity_id": opportunity.id,
                "step": step,
                "regulatory_checked": task_input.get("regulatory_checked", False),
            },
        )
        if follow_up is not None:
            follow_up.status = "sent" if result.output.get("sent") else "skipped"
            if not result.output.get("sent"):
                follow_up.stop_reason = "; ".join(result.output.get("reasons", []))[:160] or "not sent"
            ctx.session.flush()
        if result.output.get("sent"):
            ctx.memory.transition(opportunity, OpportunityStage.FOLLOW_UP, f"follow-up {step} sent")
        return result
