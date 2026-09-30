"""Deterministic policy and risk gate.

Every action an agent wants to take passes through evaluate(). The LLM never
decides whether something is permitted; it only proposes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.budget.controller import BudgetController
from app.core.config import Settings, get_settings
from app.core.interfaces import ActionRequest, PolicyResult
from app.core.types import ActionKind, Decision, REGULATED_CATEGORIES, RiskLevel, utcnow
from app.database.models import Company, Contact, FollowUp, Message, SupplierRFQ, SystemState
from app.policies.fact_check import validate_message
from app.policies.licenses import coverage
from app.review.queue import approved_for, rejected

SECRET_HINTS = ("api_key", "apikey", "password", "secret", "private_key", "authorization")


@dataclass
class PolicyContext:
    session: Session
    settings: Settings
    budget: BudgetController
    now: datetime


Rule = Callable[[ActionRequest, PolicyContext], PolicyResult | None]

# Every kind of outbound message. Opt-out, duplicate, licence and fact checks
# apply to all of them; rate limits and follow-up caps only to unsolicited mail.
SEND_KINDS = (ActionKind.SEND_OUTREACH, ActionKind.SEND_FOLLOWUP, ActionKind.SEND_REPLY, ActionKind.SEND_SUPPLIER_RFQ)
# Messages to buyers: licence scope and regulatory checks apply to these.
BUYER_SEND_KINDS = (ActionKind.SEND_OUTREACH, ActionKind.SEND_FOLLOWUP, ActionKind.SEND_REPLY)


def _block(rule: str, reason: str, risk: RiskLevel = RiskLevel.HIGH) -> PolicyResult:
    return PolicyResult(decision=Decision.BLOCK, reasons=[reason], rule_ids=[rule], risk=risk)


def _escalate(rule: str, reason: str, risk: RiskLevel = RiskLevel.HIGH) -> PolicyResult:
    return PolicyResult(decision=Decision.ESCALATE, reasons=[reason], rule_ids=[rule], risk=risk)


# ------------------------------------------------------------------ rules
def rule_emergency_stop(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    state = ctx.session.get(SystemState, "control")
    if not state:
        return None
    value = state.value or {}
    if value.get("emergency_stop"):
        return _block("R-SYS-01", "emergency stop engaged")
    if value.get("paused") and req.kind != ActionKind.RESEARCH:
        return _block("R-SYS-02", "system paused")
    category = req.payload.get("product_category")
    if category and category in (value.get("paused_categories") or []):
        return _block("R-SYS-03", f"category paused: {category}")
    country = req.payload.get("country")
    if country and country in (value.get("paused_geographies") or []):
        return _block("R-SYS-04", f"geography paused: {country}")
    return None


def rule_no_secrets(req: ActionRequest, _ctx: PolicyContext) -> PolicyResult | None:
    flat = " ".join(f"{k}={v}" for k, v in req.payload.items()).lower()
    for hint in SECRET_HINTS:
        if hint in flat:
            return _block("R-SEC-01", f"action payload appears to carry a credential ({hint})")
    return None


def rule_destructive(req: ActionRequest, _ctx: PolicyContext) -> PolicyResult | None:
    if req.kind == ActionKind.DESTRUCTIVE:
        return _block("R-DES-01", "destructive actions are never autonomous")
    return None


def rule_commitments(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    if req.kind == ActionKind.LEGAL_COMMITMENT:
        return _escalate("R-LEG-01", "legal commitments require human authorisation")
    if req.kind in (ActionKind.FINANCIAL_COMMITMENT, ActionKind.EXTERNAL_PURCHASE):
        threshold = ctx.settings.require_human_approval_above_usd
        amount = float(req.payload.get("amount_usd", req.estimated_cost_usd))
        if amount > threshold:
            return _escalate(
                "R-FIN-01",
                f"financial commitment of USD {amount:.2f} exceeds autonomous threshold {threshold:.2f}",
            )
    return None


def rule_license(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    """Regulated trade only where an active licence covers the country and category.

    Outreach outside licence coverage escalates (a partner route may exist);
    a commitment outside coverage is blocked outright. A cross-border
    commitment inside coverage escalates with the destination-country checks.
    """
    category = req.payload.get("product_category")
    if category not in {c.value for c in REGULATED_CATEGORIES}:
        return None
    contact_kinds = BUYER_SEND_KINDS
    commitment_kinds = (ActionKind.FINANCIAL_COMMITMENT, ActionKind.LEGAL_COMMITMENT)
    if req.kind not in contact_kinds + commitment_kinds:
        return None
    result = coverage(ctx.session, req.payload.get("country"), category, ctx.now.date())
    if result.covered and result.cross_border and req.kind in commitment_kinds:
        return _escalate(
            "R-REG-06",
            f"cross-border regulated commitment ({result.reason}): confirm the buyer holds import "
            f"authorisation in {req.payload.get('country')} and the product is registered with that "
            "country's regulator; your licence does not authorise import there",
        )
    if result.covered:
        return None
    if req.kind in commitment_kinds:
        return _block("R-REG-05", f"regulated commitment outside licence coverage: {result.reason}")
    return _escalate("R-REG-04", f"regulated contact outside licence coverage: {result.reason}")


def rule_rejected_by_operator(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    """An action the operator rejected in review is never proposed again."""
    item = rejected(ctx.session, req.idempotency_key)
    if item is not None:
        return _block("R-REV-01", f"operator rejected this action in review {item.id}")
    return None


def rule_outreach_approval_mode(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    """REQUIRE_OUTREACH_APPROVAL=true: the operator approves every unsolicited e-mail."""
    if ctx.settings.require_outreach_approval and req.kind in (
        ActionKind.SEND_OUTREACH, ActionKind.SEND_FOLLOWUP, ActionKind.SEND_SUPPLIER_RFQ
    ):
        return _escalate("R-APPR-01", "approval mode is on: every outreach e-mail waits for you", RiskLevel.LOW)
    return None


def rule_reply_needs_human(req: ActionRequest, _ctx: PolicyContext) -> PolicyResult | None:
    """Answers to buyers carry prices and terms: a human approves every one."""
    if req.kind == ActionKind.SEND_REPLY:
        return _escalate("R-REPLY-01", "replies to buyers (quotes, terms, information) need your approval", RiskLevel.MEDIUM)
    return None


def rule_regulated(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    category = req.payload.get("product_category")
    regulated = category in {c.value for c in REGULATED_CATEGORIES}
    if req.kind == ActionKind.REGULATED_TRANSACTION or (
        regulated and req.kind in (ActionKind.FINANCIAL_COMMITMENT, ActionKind.LEGAL_COMMITMENT)
    ):
        if not ctx.settings.allow_regulated_autonomous_transactions:
            return _escalate("R-REG-01", "regulated transaction requires compliance review")
    if regulated and req.kind in BUYER_SEND_KINDS:
        if not req.payload.get("regulatory_checked"):
            return _escalate(
                "R-REG-02",
                f"regulatory requirements for {category} not verified before contact",
            )
        if req.payload.get("controlled_substance"):
            return _escalate("R-REG-03", "controlled/restricted product flagged")
    return None


def rule_opt_out(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    if req.kind not in SEND_KINDS:
        return None
    contact_id = req.payload.get("contact_id")
    if contact_id:
        contact = ctx.session.get(Contact, contact_id)
        if contact is None:
            return _block("R-OUT-00", "contact does not exist")
        if contact.opted_out:
            return _block("R-OUT-01", "contact has opted out")
        if contact.bounced:
            return _block("R-OUT-02", "contact address previously bounced")
        if not contact.email:
            return _block("R-OUT-03", "no verified email address for contact")
    company_id = req.payload.get("company_id")
    if company_id:
        company = ctx.session.get(Company, company_id)
        if company is not None and company.opted_out:
            return _block("R-OUT-04", "company has opted out")
    return None


def rule_duplicate(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    if req.kind not in SEND_KINDS:
        return None
    key = req.idempotency_key
    if not key:
        return None
    existing = ctx.session.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.dedupe_key == key, Message.direction == "outbound")
    )
    if existing:
        return _block("R-DUP-01", "identical outbound message already exists", RiskLevel.MEDIUM)
    return None


def rule_rate_limits(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    if req.kind not in (ActionKind.SEND_OUTREACH, ActionKind.SEND_FOLLOWUP):
        return None
    since = ctx.now - timedelta(days=1)
    sent_today = ctx.session.scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.direction == "outbound",
            Message.status == "sent",
            Message.sent_at.is_not(None),
            Message.sent_at >= since,
        )
    )
    if sent_today >= ctx.settings.outreach_daily_limit:
        return _block("R-RATE-01", "daily outreach limit reached", RiskLevel.MEDIUM)

    contact_id = req.payload.get("contact_id")
    if contact_id:
        per_contact = ctx.session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.direction == "outbound",
                Message.contact_id == contact_id,
                Message.sent_at.is_not(None),
                Message.sent_at >= since,
            )
        )
        if per_contact >= ctx.settings.outreach_per_company_day_limit:
            return _block("R-RATE-02", "per-contact daily frequency limit reached", RiskLevel.MEDIUM)
    return None


def rule_supplier_rfq_limits(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    """Supplier RFQs have their own daily cap and follow-up limit."""
    if req.kind != ActionKind.SEND_SUPPLIER_RFQ:
        return None
    since = ctx.now - timedelta(days=1)
    sent_today = ctx.session.scalar(
        select(func.count()).select_from(SupplierRFQ).where(
            SupplierRFQ.last_sent_at.is_not(None), SupplierRFQ.last_sent_at >= since
        )
    ) or 0
    if sent_today >= ctx.settings.supplier_rfq_daily_limit:
        return _block("R-RATE-03", "daily supplier RFQ limit reached", RiskLevel.MEDIUM)
    if int(req.payload.get("step", 0)) > ctx.settings.supplier_rfq_max_followups:
        return _block("R-FUP-03", "maximum supplier follow-ups reached", RiskLevel.LOW)
    return None


def rule_followup_cap(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    if req.kind != ActionKind.SEND_FOLLOWUP:
        return None
    opportunity_id = req.payload.get("opportunity_id") or req.opportunity_id
    if not opportunity_id:
        return None
    step = int(req.payload.get("step", 1))
    if step > ctx.settings.outreach_max_followups:
        return _block("R-FUP-01", "maximum follow-up attempts reached", RiskLevel.LOW)
    stopped = ctx.session.scalar(
        select(func.count())
        .select_from(FollowUp)
        .where(FollowUp.opportunity_id == opportunity_id, FollowUp.status == "stopped")
    )
    if stopped:
        return _block("R-FUP-02", "follow-up sequence already stopped for this opportunity")
    return None


def rule_fact_validation(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    if req.kind not in SEND_KINDS:
        return None
    facts = dict(req.payload.get("allowed_facts") or {})
    # Text the operator read and approved (possibly edited) is theirs: figures
    # in it are not model assertions. Claim checks (medical, regulatory,
    # licence, relationship) still apply. Verified here, not trusted from payload.
    facts["operator_approved"] = approved_for(
        ctx.session, req.payload.get("approved_review_id"), req.idempotency_key
    ) is not None
    result = validate_message(req.payload.get("subject", ""), req.payload.get("body", ""), facts)
    if not result.ok:
        return PolicyResult(
            decision=Decision.BLOCK,
            reasons=[f"unverified content: {', '.join(result.unsupported[:5])}"],
            rule_ids=["R-FACT-01"],
            risk=RiskLevel.HIGH,
        )
    if not req.payload.get("personalized", True):
        return _block("R-FACT-02", "message is not personalised to the recipient", RiskLevel.MEDIUM)
    return None


def rule_budget(req: ActionRequest, ctx: PolicyContext) -> PolicyResult | None:
    if req.estimated_cost_usd <= 0:
        return None
    if not ctx.budget.can_spend(req.cost_category, req.estimated_cost_usd):
        return _block(
            "R-BUD-01",
            f"budget exhausted for {req.cost_category} (need {req.estimated_cost_usd:.4f} USD)",
            RiskLevel.MEDIUM,
        )
    return None


DEFAULT_RULES: list[Rule] = [
    rule_emergency_stop,
    rule_no_secrets,
    rule_destructive,
    rule_commitments,
    rule_rejected_by_operator,
    rule_reply_needs_human,
    rule_outreach_approval_mode,
    rule_regulated,
    rule_license,
    rule_opt_out,
    rule_duplicate,
    rule_rate_limits,
    rule_supplier_rfq_limits,
    rule_followup_cap,
    rule_fact_validation,
    rule_budget,
]


class PolicyEngine:
    def __init__(
        self,
        session: Session,
        budget: BudgetController,
        settings: Settings | None = None,
        rules: list[Rule] | None = None,
    ) -> None:
        self.session = session
        self.budget = budget
        self.settings = settings or get_settings()
        self.rules = rules or list(DEFAULT_RULES)

    def evaluate(self, request: ActionRequest, now: datetime | None = None) -> PolicyResult:
        ctx = PolicyContext(
            session=self.session,
            settings=self.settings,
            budget=self.budget,
            now=now or utcnow(),
        )
        blocks: list[PolicyResult] = []
        escalations: list[PolicyResult] = []
        for rule in self.rules:
            outcome = rule(request, ctx)
            if outcome is None:
                continue
            if outcome.decision == Decision.BLOCK:
                blocks.append(outcome)
            elif outcome.decision == Decision.ESCALATE:
                escalations.append(outcome)
        if blocks:
            return PolicyResult(
                decision=Decision.BLOCK,
                reasons=[r for b in blocks for r in b.reasons],
                rule_ids=[r for b in blocks for r in b.rule_ids],
                risk=RiskLevel.HIGH,
            )
        if escalations:
            approval = approved_for(
                self.session, request.payload.get("approved_review_id"), request.idempotency_key
            )
            if approval is not None:
                return PolicyResult(
                    decision=Decision.ALLOW,
                    reasons=[f"escalation waived by operator approval {approval.id}"]
                    + [r for e in escalations for r in e.reasons],
                    rule_ids=["R-REV-02"] + [r for e in escalations for r in e.rule_ids],
                    risk=request.risk,
                )
            return PolicyResult(
                decision=Decision.ESCALATE,
                reasons=[r for e in escalations for r in e.reasons],
                rule_ids=[r for e in escalations for r in e.rule_ids],
                risk=RiskLevel.HIGH,
            )
        return PolicyResult(decision=Decision.ALLOW, risk=request.risk)
