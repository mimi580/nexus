"""Persistent schema. Every lifecycle transition, cost and claim is durable."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import new_id
from app.core.types import utcnow
from app.database.base import Base, TimestampMixin


def _pk(prefix: str):
    return mapped_column(String(40), primary_key=True, default=lambda: new_id(prefix))


class Objective(Base, TimestampMixin):
    __tablename__ = "objectives"
    id: Mapped[str] = _pk("obj")
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tasks: Mapped[list["Task"]] = relationship(back_populates="objective")


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id: Mapped[str] = _pk("tsk")
    objective_id: Mapped[str | None] = mapped_column(ForeignKey("objectives.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(40), index=True)
    agent: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    objective: Mapped[Objective | None] = relationship(back_populates="tasks")

    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),)


class MarketAssessment(Base, TimestampMixin):
    __tablename__ = "market_assessments"
    id: Mapped[str] = _pk("mkt")
    country: Mapped[str] = mapped_column(String(80), index=True)
    region: Mapped[str] = mapped_column(String(80), default="")
    product_category: Mapped[str] = mapped_column(String(50), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    factors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    id: Mapped[str] = _pk("cmp")
    name: Mapped[str] = mapped_column(String(300))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(200), index=True)
    country: Mapped[str | None] = mapped_column(String(80), index=True)
    city: Mapped[str | None] = mapped_column(String(120))
    segment: Mapped[str | None] = mapped_column(String(80), index=True)
    size_indicator: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text, default="")
    buying_signals: Mapped[list[Any]] = mapped_column(JSON, default=list)
    source: Mapped[str | None] = mapped_column(String(200))
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"
    id: Mapped[str] = _pk("ctc")
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(200), index=True)
    phone: Mapped[str | None] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_id: Mapped[str | None] = mapped_column(String(40))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bounced: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(200))


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    id: Mapped[str] = _pk("prd")
    category: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(300))
    brand: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    condition: Mapped[str | None] = mapped_column(String(60))
    specification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quantity_available: Mapped[int | None] = mapped_column(Integer)
    acquisition_cost_usd: Mapped[float | None] = mapped_column(Float)
    target_price_usd: Mapped[float | None] = mapped_column(Float)
    minimum_margin_pct: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    location: Mapped[str | None] = mapped_column(String(120))
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))
    warranty: Mapped[str | None] = mapped_column(String(160))
    shipping_terms: Mapped[str | None] = mapped_column(String(160))
    payment_terms: Mapped[str | None] = mapped_column(String(160))
    regulatory: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    documentation: Mapped[list[Any]] = mapped_column(JSON, default=list)
    availability: Mapped[str | None] = mapped_column(String(60))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    id: Mapped[str] = _pk("sup")
    name: Mapped[str] = mapped_column(String(300))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    country: Mapped[str | None] = mapped_column(String(80))
    categories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.5)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    payment_terms: Mapped[str | None] = mapped_column(String(160))
    documents: Mapped[list[Any]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BuyerRequirement(Base, TimestampMixin):
    __tablename__ = "buyer_requirements"
    id: Mapped[str] = _pk("req")
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    product_category: Mapped[str] = mapped_column(String(50), index=True)
    specification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quantity: Mapped[int | None] = mapped_column(Integer)
    target_price_usd: Mapped[float | None] = mapped_column(Float)
    timing: Mapped[str | None] = mapped_column(String(120))
    evidence_id: Mapped[str | None] = mapped_column(String(40))


class Opportunity(Base, TimestampMixin):
    __tablename__ = "opportunities"
    id: Mapped[str] = _pk("opp")
    objective_id: Mapped[str | None] = mapped_column(ForeignKey("objectives.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))
    product_category: Mapped[str] = mapped_column(String(50), index=True)
    stage: Mapped[str] = mapped_column(String(30), default="discovered", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    qualification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    economics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id"))
    estimated_value_usd: Mapped[float | None] = mapped_column(Float)
    estimated_margin_usd: Mapped[float | None] = mapped_column(Float)
    compliance_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class StageTransition(Base, TimestampMixin):
    __tablename__ = "stage_transitions"
    id: Mapped[str] = _pk("stg")
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    from_stage: Mapped[str | None] = mapped_column(String(30))
    to_stage: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(60), default="system")


class Interaction(Base, TimestampMixin):
    __tablename__ = "interactions"
    id: Mapped[str] = _pk("int")
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), index=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))
    direction: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(30), default="email")
    category: Mapped[str | None] = mapped_column(String(40), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    id: Mapped[str] = _pk("msg")
    opportunity_id: Mapped[str | None] = mapped_column(ForeignKey("opportunities.id"), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    direction: Mapped[str] = mapped_column(String(20), index=True)
    channel: Mapped[str] = mapped_column(String(30), default="email")
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    sequence_step: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    provider: Mapped[str | None] = mapped_column(String(60))
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    fact_check: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_messages_dedupe_direction", "dedupe_key", "direction"),)


class FollowUp(Base, TimestampMixin):
    __tablename__ = "follow_ups"
    id: Mapped[str] = _pk("fup")
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"))
    step: Mapped[int] = mapped_column(Integer, default=1)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    stop_reason: Mapped[str | None] = mapped_column(String(160))


class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"
    id: Mapped[str] = _pk("str")
    name: Mapped[str] = mapped_column(String(160), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rationale: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("name", "version", name="uq_strategies_name_version"),)


class Experiment(Base, TimestampMixin):
    __tablename__ = "experiments"
    id: Mapped[str] = _pk("exp")
    strategy_name: Mapped[str] = mapped_column(String(160), index=True)
    control_version: Mapped[int] = mapped_column(Integer)
    variant_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    metric: Mapped[str] = mapped_column(String(60), default="qualified_response_rate")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision: Mapped[str | None] = mapped_column(String(30))


class Outcome(Base, TimestampMixin):
    __tablename__ = "outcomes"
    id: Mapped[str] = _pk("out")
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), index=True)
    result: Mapped[str] = mapped_column(String(30), index=True)
    revenue_usd: Mapped[float | None] = mapped_column(Float)
    margin_usd: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    strategy_id: Mapped[str | None] = mapped_column(String(40))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ModelRun(Base, TimestampMixin):
    __tablename__ = "model_runs"
    id: Mapped[str] = _pk("run")
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    tier: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(60), index=True)
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)


class CostEntry(Base, TimestampMixin):
    __tablename__ = "cost_entries"
    id: Mapped[str] = _pk("cst")
    period: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    category: Mapped[str] = mapped_column(String(40), index=True)
    amount_usd: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(20), default="committed", index=True)
    reason: Mapped[str] = mapped_column(String(300), default="")
    reference: Mapped[str | None] = mapped_column(String(80))


class BudgetPeriod(Base, TimestampMixin):
    __tablename__ = "budget_periods"
    id: Mapped[str] = _pk("bdg")
    period: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    limit_usd: Mapped[float] = mapped_column(Float)
    category_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hard_stopped: Mapped[bool] = mapped_column(Boolean, default=False)


class Evidence(Base, TimestampMixin):
    __tablename__ = "evidence"
    id: Mapped[str] = _pk("evd")
    claim: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    source: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(40), default="model")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    verification: Mapped[str] = mapped_column(String(20), default="unverified", index=True)
    subject_type: Mapped[str | None] = mapped_column(String(40), index=True)
    subject_id: Mapped[str | None] = mapped_column(String(40), index=True)


class ComplianceEvent(Base, TimestampMixin):
    __tablename__ = "compliance_events"
    id: Mapped[str] = _pk("cmp")
    opportunity_id: Mapped[str | None] = mapped_column(String(40), index=True)
    product_category: Mapped[str | None] = mapped_column(String(50))
    flag: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    detail: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class License(Base, TimestampMixin):
    """A trading licence held by the operator (importer, distributor, wholesaler).

    Regulated outreach and transactions are only permitted in a country and
    product category an active, unexpired licence covers.
    """

    __tablename__ = "licenses"
    __table_args__ = (UniqueConstraint("country", "license_number", name="uq_license_country_number"),)
    id: Mapped[str] = _pk("lic")
    holder_name: Mapped[str] = mapped_column(String(200))
    license_types: Mapped[list[Any]] = mapped_column(JSON, default=list)
    country: Mapped[str] = mapped_column(String(80), index=True)
    issuing_authority: Mapped[str] = mapped_column(String(200))
    license_number: Mapped[str] = mapped_column(String(120))
    product_categories: Mapped[list[Any]] = mapped_column(JSON, default=list)
    scope_notes: Mapped[str] = mapped_column(Text, default="")
    valid_from: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date, index=True)
    document_ref: Mapped[str | None] = mapped_column(String(500))
    verification: Mapped[str] = mapped_column(String(20), default="user_provided")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class AuditEvent(Base, TimestampMixin):
    __tablename__ = "audit_events"
    id: Mapped[str] = _pk("aud")
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(80), default="nexus")
    objective_id: Mapped[str | None] = mapped_column(String(40), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), index=True)
    decision: Mapped[str | None] = mapped_column(String(20), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SystemState(Base, TimestampMixin):
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ScheduledJob(Base, TimestampMixin):
    __tablename__ = "scheduled_jobs"
    id: Mapped[str] = _pk("job")
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_key: Mapped[str | None] = mapped_column(String(64))
