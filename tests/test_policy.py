import pytest

from app.core.interfaces import ActionRequest
from app.core.types import ActionKind, Decision, ProductCategory
from app.database.models import Contact, Message
from app.policies.fact_check import validate_message


def _company(ctx, **kw):
    company, _ = ctx.memory.upsert_company(name=kw.get("name", "Test Clinic"), domain="test.example", country="Kenya")
    return company


def _contact(ctx, company, **kw):
    return ctx.memory.upsert_contact(
        company_id=company.id,
        full_name="Test Person",
        role="Procurement",
        email=kw.get("email", "test.person@test.example"),
        confidence=0.7,
        source="directory",
    )


def _outreach(contact, company, **payload):
    base = {
        "contact_id": contact.id,
        "company_id": company.id,
        "subject": "Laptop supply",
        "body": "Hello, we can quote against your requirement. Reply unsubscribe to opt out.",
        "allowed_facts": {"numbers": []},
        "personalized": True,
    }
    base.update(payload)
    return ActionRequest(
        kind=ActionKind.SEND_OUTREACH,
        summary="test send",
        payload=base,
        estimated_cost_usd=0.01,
        cost_category="email",
        idempotency_key=payload.get("idempotency_key", "key-1"),
    )


def test_opted_out_contact_is_blocked(ctx):
    company = _company(ctx)
    contact = _contact(ctx, company)
    ctx.memory.opt_out_contact(contact.id)
    result = ctx.policy.evaluate(_outreach(contact, company), now=ctx.now)
    assert result.decision == Decision.BLOCK
    assert "R-OUT-01" in result.rule_ids


def test_duplicate_message_is_blocked(ctx):
    company = _company(ctx)
    contact = _contact(ctx, company)
    ctx.session.add(
        Message(contact_id=contact.id, direction="outbound", status="sent", dedupe_key="key-1", sent_at=ctx.now)
    )
    ctx.session.flush()
    result = ctx.policy.evaluate(_outreach(contact, company), now=ctx.now)
    assert result.decision == Decision.BLOCK
    assert "R-DUP-01" in result.rule_ids


def test_daily_rate_limit_is_enforced(ctx):
    company = _company(ctx)
    contact = _contact(ctx, company)
    for i in range(ctx.settings.outreach_daily_limit):
        other = ctx.memory.upsert_contact(
            company_id=company.id, full_name=f"P{i}", email=f"p{i}@test.example", source="d"
        )
        ctx.session.add(
            Message(contact_id=other.id, direction="outbound", status="sent", dedupe_key=f"k{i}", sent_at=ctx.now)
        )
    ctx.session.flush()
    result = ctx.policy.evaluate(_outreach(contact, company, idempotency_key="fresh"), now=ctx.now)
    assert result.decision == Decision.BLOCK
    assert "R-RATE-01" in result.rule_ids


def test_regulated_outreach_without_verification_escalates(ctx):
    company = _company(ctx)
    contact = _contact(ctx, company)
    request = _outreach(
        contact,
        company,
        product_category=ProductCategory.PHARMA.value,
        regulatory_checked=False,
        idempotency_key="pharma-1",
    )
    result = ctx.policy.evaluate(request, now=ctx.now)
    assert result.decision == Decision.ESCALATE
    assert "R-REG-02" in result.rule_ids


def test_financial_and_legal_commitments_escalate(ctx):
    for kind in (ActionKind.FINANCIAL_COMMITMENT, ActionKind.LEGAL_COMMITMENT):
        result = ctx.policy.evaluate(
            ActionRequest(kind=kind, summary="commit", payload={"amount_usd": 5000}), now=ctx.now
        )
        assert result.decision == Decision.ESCALATE


def test_destructive_actions_and_secrets_are_blocked(ctx):
    destructive = ctx.policy.evaluate(
        ActionRequest(kind=ActionKind.DESTRUCTIVE, summary="drop table"), now=ctx.now
    )
    assert destructive.decision == Decision.BLOCK
    leaky = ctx.policy.evaluate(
        ActionRequest(kind=ActionKind.CRM_WRITE, summary="write", payload={"api_key": "sk-live-123"}),
        now=ctx.now,
    )
    assert leaky.decision == Decision.BLOCK


def test_emergency_stop_blocks_everything(ctx):
    ctx.memory.set_control_state(emergency_stop=True)
    result = ctx.policy.evaluate(ActionRequest(kind=ActionKind.CRM_WRITE, summary="x"), now=ctx.now)
    assert result.decision == Decision.BLOCK
    assert "R-SYS-01" in result.rule_ids


def test_paused_category_blocks_that_category_only(ctx):
    ctx.memory.set_control_state(paused_categories=[ProductCategory.PHARMA.value])
    blocked = ctx.policy.evaluate(
        ActionRequest(kind=ActionKind.RESEARCH, summary="x", payload={"product_category": ProductCategory.PHARMA.value}),
        now=ctx.now,
    )
    allowed = ctx.policy.evaluate(
        ActionRequest(kind=ActionKind.RESEARCH, summary="x", payload={"product_category": ProductCategory.LAPTOP.value}),
        now=ctx.now,
    )
    assert blocked.decision == Decision.BLOCK
    assert allowed.decision == Decision.ALLOW


def test_budget_exhaustion_blocks_paid_actions(ctx):
    for category, limit in ctx.settings.category_limits.items():
        rid = ctx.budget.reserve(category, limit, "exhaust")
        ctx.budget.commit(rid, limit)
    result = ctx.policy.evaluate(
        ActionRequest(kind=ActionKind.MODEL_CALL, summary="call", estimated_cost_usd=0.5, cost_category="ai_primary"),
        now=ctx.now,
    )
    assert result.decision == Decision.BLOCK
    assert "R-BUD-01" in result.rule_ids


@pytest.mark.parametrize(
    "subject,body,facts",
    [
        ("Offer", "We are an authorized distributor for this brand.", {}),
        ("Offer", "This device is FDA approved for all uses.", {}),
        ("Offer", "We have 450 units in stock today.", {"numbers": []}),
        ("Offer", "This treats all infections with no side effects.", {}),
    ],
)
def test_unverified_claims_are_blocked(ctx, subject, body, facts):
    company = _company(ctx)
    contact = _contact(ctx, company)
    request = _outreach(contact, company, subject=subject, body=body, allowed_facts=facts, idempotency_key=body[:10])
    result = ctx.policy.evaluate(request, now=ctx.now)
    assert result.decision == Decision.BLOCK
    assert "R-FACT-01" in result.rule_ids


def test_supported_figures_pass_fact_check():
    result = validate_message("Quote", "We can quote for 40 units with a 12 day lead time.", {"numbers": [40, 12]})
    assert result.ok is True
