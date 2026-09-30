"""One path for every outbound e-mail: footer, unsubscribe link, threading,
budget, provider call, bounce handling and (in simulation) the simulated reply.

Policy has already been evaluated by the caller; this function only delivers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.ids import stable_key
from app.database.models import Contact, Interaction, Message, Opportunity
from app.execution.email import compliance_footer, new_message_id, unsubscribe_url

SEND_COST_USD = 0.01  # per message, charged to the email category


def send_email(
    ctx: Any,
    contact: Contact,
    subject: str,
    body: str,
    dedupe_key: str,
    *,
    opportunity: Opportunity | None = None,
    step: int = 0,
    fact_check: dict | None = None,
    in_reply_to: str | None = None,
    product_category: str | None = None,
    counterparty: str = "buyer",
) -> Message:
    fact_check = fact_check or {}

    settings = ctx.settings
    link = unsubscribe_url(settings, contact.id)
    if settings.business_name or settings.nexus_mode == "production":
        body = body.rstrip() + compliance_footer(settings, link)
    message_id = new_message_id(settings)
    thread = select(Message).where(
        Message.contact_id == contact.id, Message.direction == "outbound", Message.status == "sent",
        Message.provider_message_id.is_not(None),
    )
    if opportunity is not None:
        thread = thread.where(Message.opportunity_id == opportunity.id)
    previous = ctx.session.scalar(thread.order_by(Message.sent_at.desc()))
    message = Message(
        opportunity_id=opportunity.id if opportunity is not None else None,
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
        context={
            "product_category": opportunity.product_category if opportunity is not None else product_category,
            "counterparty": counterparty,
            "step": step,
            "contact_id": contact.id,
            "message_id": message_id,
            "unsubscribe_url": link,
            "in_reply_to": in_reply_to or (previous.provider_message_id if previous else None),
        },
    )
    message.provider = result.provider
    message.simulated = result.simulated
    if not result.ok:
        message.status = "failed"
        message.error = result.error
        ctx.budget.release(reservation)
        if result.permanent_failure:
            contact.bounced = True
            ctx.audit.record(
                "hard_bounce", summary=f"{contact.email} refused by the receiving server",
                decision="block", task_id=ctx.task_id, contact_id=contact.id,
            )
        return message

    ctx.budget.commit(reservation, max(result.cost_usd, SEND_COST_USD))
    message.status = "sent"
    message.sent_at = ctx.now
    message.provider_message_id = result.provider_message_id
    ctx.session.add(
        Interaction(
            opportunity_id=opportunity.id if opportunity is not None else None,
            company_id=contact.company_id,
            contact_id=contact.id,
            direction="outbound",
            summary=subject,
            payload={"step": step, "counterparty": counterparty},
        )
    )
    ctx.session.flush()

    if result.simulated_reply:
        inbound = Message(
            opportunity_id=opportunity.id if opportunity is not None else None,
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
            objective_id=opportunity.objective_id if opportunity is not None else None,
            task_input={"message_id": inbound.id},
            priority=80,
            idempotency_key=stable_key("response", inbound.id),
            scheduled_for=ctx.now + timedelta(hours=result.reply_delay_hours or 6),
        )
    return message
