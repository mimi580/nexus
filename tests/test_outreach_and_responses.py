from sqlalchemy import func, select

from app.agents.outreach import OutreachAgent
from app.agents.response import ResponseAgent
from app.core.types import OpportunityStage, ProductCategory
from app.database.models import ComplianceEvent, Contact, FollowUp, Message


def _prepared_opportunity(ctx, category=ProductCategory.LAPTOP.value):
    company, _ = ctx.memory.upsert_company(name="Buyer Co", domain="buyer.example", country="Kenya")
    contact = ctx.memory.upsert_contact(
        company_id=company.id, full_name="Amina Yusuf", role="IT Manager",
        email="amina.yusuf@buyer.example", confidence=0.7, source="directory",
    )
    opportunity, _ = ctx.memory.create_opportunity(company.id, category)
    opportunity.contact_id = contact.id
    opportunity.economics = {"quantity": 40, "lead_time_days": 12}
    opportunity.qualification = {"strategy": {"message_angle": "lead time", "followup_cadence_days": 4}}
    ctx.session.flush()
    return opportunity, company, contact


def test_outreach_sends_once_and_schedules_a_follow_up(ctx):
    opportunity, _, contact = _prepared_opportunity(ctx)
    result = OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert result.output["sent"] is True
    assert opportunity.stage in (OpportunityStage.OUTREACH.value, OpportunityStage.FOLLOW_UP.value)
    assert ctx.session.scalar(select(func.count()).select_from(FollowUp)) == 1

    repeat = OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert repeat.output.get("sent") is False
    sent = ctx.session.scalar(
        select(func.count()).select_from(Message).where(Message.direction == "outbound", Message.status == "sent")
    )
    assert sent == 1


def test_outreach_to_opted_out_contact_never_sends(ctx):
    opportunity, _, contact = _prepared_opportunity(ctx)
    ctx.memory.opt_out_contact(contact.id)
    result = OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert result.output["sent"] is False
    assert ctx.session.scalar(select(func.count()).select_from(Message).where(Message.direction == "outbound")) == 0


def test_pharma_outreach_without_verification_is_escalated(ctx):
    opportunity, _, _ = _prepared_opportunity(ctx, ProductCategory.PHARMA.value)
    result = OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": False})
    assert result.output["escalated"] is True
    assert opportunity.stage == OpportunityStage.COMMERCIAL_REVIEW.value


def _inbound(ctx, opportunity, contact, body):
    message = Message(
        opportunity_id=opportunity.id, contact_id=contact.id, direction="inbound",
        body=body, status="received", dedupe_key=f"in-{body[:8]}",
    )
    ctx.session.add(message)
    ctx.session.add(FollowUp(opportunity_id=opportunity.id, contact_id=contact.id, step=1, due_at=ctx.now))
    ctx.session.flush()
    return message


def test_unsubscribe_suppresses_the_contact_and_stops_follow_ups(ctx):
    opportunity, _, contact = _prepared_opportunity(ctx)
    message = _inbound(ctx, opportunity, contact, "Please remove me from your list and do not contact me again.")
    result = ResponseAgent().run(ctx, {"message_id": message.id})
    assert result.output["category"] == "unsubscribe"
    assert ctx.session.get(Contact, contact.id).opted_out is True
    assert all(f.status == "stopped" for f in ctx.session.scalars(select(FollowUp)))
    assert opportunity.stage == OpportunityStage.LOST.value


def test_regulatory_reply_raises_a_compliance_event(ctx):
    opportunity, _, contact = _prepared_opportunity(ctx, ProductCategory.PHARMA.value)
    message = _inbound(ctx, opportunity, contact, "Any supply requires registration with our national regulator.")
    result = ResponseAgent().run(ctx, {"message_id": message.id})
    assert result.output["category"] == "regulatory_issue"
    assert opportunity.stage == OpportunityStage.COMMERCIAL_REVIEW.value
    assert ctx.session.scalar(select(func.count()).select_from(ComplianceEvent)) >= 1


def test_wrong_contact_reroutes_to_decision_maker_discovery(ctx):
    opportunity, _, contact = _prepared_opportunity(ctx)
    message = _inbound(ctx, opportunity, contact, "I am not the right person for this. Procurement handles purchasing.")
    result = ResponseAgent().run(ctx, {"message_id": message.id})
    assert result.output["category"] == "wrong_contact"
    assert result.next_tasks[0]["agent"] == "decision_maker_discovery"


def test_positive_reply_is_handed_to_a_human(ctx):
    from app.database.models import AuditEvent

    opportunity, _, contact = _prepared_opportunity(ctx)
    message = _inbound(ctx, opportunity, contact, "This is relevant to us - we are reviewing options this quarter.")
    result = ResponseAgent().run(ctx, {"message_id": message.id})
    assert result.output["category"] == "interested"
    handoffs = ctx.session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "human_handoff_required")
    )
    assert handoffs == 1
