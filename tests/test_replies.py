"""Drafted replies to interested buyers: pricing in code, approval by a human, editable drafts."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents.reply import ReplyAgent, suggest_pricing
from app.agents.response import ResponseAgent
from app.core.interfaces import ActionRequest
from app.core.types import ActionKind, Decision, OpportunityStage, ProductCategory
from app.database.models import Message, ReviewItem, Task
from app.review import queue

LAPTOP = ProductCategory.LAPTOP.value


def _deal(ctx):
    company, _ = ctx.memory.upsert_company(name="Kigali Academy", domain="kigaliacademy.example", country="Rwanda")
    contact = ctx.memory.upsert_contact(
        company_id=company.id, full_name="Grace Uwase", role="Bursar", email="grace@kigaliacademy.example",
        confidence=0.7, source="https://kigaliacademy.example/contact",
    )
    opportunity, _ = ctx.memory.create_opportunity(company.id, LAPTOP)
    opportunity.contact_id = contact.id
    opportunity.economics = {
        "quantity": 40, "landed_cost_usd": [8000, 8400], "revenue_usd": [12400, 13600], "min_margin_pct": 12,
        "lead_time_days": 10, "condition": "grade A refurbished", "unknowns": [],
    }
    ctx.memory.transition(opportunity, OpportunityStage.OUTREACH, "test")
    inbound = Message(
        opportunity_id=opportunity.id, contact_id=contact.id, direction="inbound", subject="Re: Laptop supply",
        body="Please send your pricing for 40 units delivered to Kigali.", status="received",
        provider_message_id="<their-reply@kigaliacademy.example>", dedupe_key="in-1",
    )
    ctx.session.add(inbound)
    ctx.session.flush()
    return opportunity, contact, inbound


def _run_response(ctx, inbound):
    task, _ = ctx.tasks.create_task(agent="response", objective_id=None, task_input={"message_id": inbound.id})
    ctx.task_id = task.id
    try:
        return ResponseAgent().run(ctx, {"message_id": inbound.id})
    finally:
        ctx.task_id = None


def test_price_is_suggested_in_code_from_cost_margin_and_price_book(ctx):
    opportunity, _, _ = _deal(ctx)
    pricing = suggest_pricing(ctx, opportunity)
    assert pricing["unit_landed_cost_usd"] == 210.0
    assert pricing["floor_unit_price_usd"] == pytest.approx(238.64, abs=0.01)
    assert pricing["suggested_unit_price_usd"] == 310.0  # floor is below the book, so the book's low end
    opportunity.economics = {**opportunity.economics, "revenue_usd": [8800, 9200]}
    squeezed = suggest_pricing(ctx, opportunity)
    assert squeezed["suggested_unit_price_usd"] == pytest.approx(238.64, abs=0.01)
    assert "above the price book" in squeezed["note"]


def test_price_request_produces_a_draft_waiting_for_approval(ctx):
    opportunity, _, inbound = _deal(ctx)
    result = _run_response(ctx, inbound)
    assert result.output["category"] == "price_request"
    item = ctx.session.scalar(select(ReviewItem))
    assert item.kind == "reply_approval" and item.status == "pending"
    assert "R-REPLY-01" in item.rule_ids
    assert "310" in item.action_payload["body"]
    assert item.action_payload["pricing"]["suggested_unit_price_usd"] == 310.0
    assert item.action_payload["their_message"].startswith("Please send your pricing")
    assert ctx.session.scalar(select(Message).where(Message.direction == "outbound")) is None


def test_replies_never_go_out_without_approval(ctx):
    opportunity, contact, _ = _deal(ctx)
    request = ActionRequest(
        kind=ActionKind.SEND_REPLY, summary="reply",
        payload={"contact_id": contact.id, "company_id": opportunity.company_id, "subject": "Re: laptops",
                 "body": "Hello, we can confirm availability.", "allowed_facts": {"numbers": []}, "personalized": True},
        idempotency_key="reply-x",
    )
    assert ctx.policy.evaluate(request, now=ctx.now).decision == Decision.ESCALATE


def test_approved_edited_reply_is_sent_threaded_and_moves_to_negotiation(ctx):
    opportunity, contact, inbound = _deal(ctx)
    _run_response(ctx, inbound)
    item = ctx.session.scalar(select(ReviewItem))
    edited = "Dear Grace,\n\nOur price is USD 329 per unit for 40 units, delivered DAP Kigali within 21 days.\n\nFrancis"
    queue.decide(ctx, item.id, "approve", note="agreed DAP", subject="Quotation: 40 laptops", body=edited)
    assert item.action_payload["operator_edited"] is True
    task = ctx.session.scalar(select(Task).where(Task.agent == "reply"))
    result = ReplyAgent().run(ctx, task.input)
    assert result.output.get("sent") is True, result.output
    sent = ctx.session.scalar(select(Message).where(Message.direction == "outbound"))
    assert sent.body.startswith(edited) and sent.subject == "Quotation: 40 laptops"
    assert opportunity.stage == OpportunityStage.NEGOTIATION.value
    assert ReplyAgent().run(ctx, task.input).output.get("sent") is False  # never twice


def test_operator_edits_still_face_claim_checks(ctx):
    opportunity, _, inbound = _deal(ctx)
    _run_response(ctx, inbound)
    item = ctx.session.scalar(select(ReviewItem))
    queue.decide(ctx, item.id, "approve", body="These laptops are an authorized distributor stock. Regards")
    task = ctx.session.scalar(select(Task).where(Task.agent == "reply"))
    result = ReplyAgent().run(ctx, task.input)
    assert result.output.get("sent") is False
    assert any("relationship_claim" in r for r in result.output["reasons"])


def test_rejected_reply_closes_the_deal(ctx):
    opportunity, _, inbound = _deal(ctx)
    _run_response(ctx, inbound)
    item = ctx.session.scalar(select(ReviewItem))
    queue.decide(ctx, item.id, "reject", note="cannot meet their delivery date")
    assert opportunity.stage == OpportunityStage.LOST.value
    with pytest.raises(queue.ReviewError):
        queue.decide(ctx, item.id, "approve")


def test_empty_edited_body_is_refused(ctx):
    _, _, inbound = _deal(ctx)
    _run_response(ctx, inbound)
    item = ctx.session.scalar(select(ReviewItem))
    with pytest.raises(queue.ReviewError, match="empty"):
        queue.decide(ctx, item.id, "approve", body="   ")
