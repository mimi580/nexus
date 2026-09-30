"""Repository over the persistent schema. Deduplication lives here."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.ids import normalize_domain, stable_key
from app.core.interfaces import EvidenceRecord
from app.core.types import OpportunityStage, utcnow
from app.database.models import (
    Company,
    ComplianceEvent,
    Contact,
    Evidence,
    Objective,
    Opportunity,
    Product,
    StageTransition,
    Supplier,
    SystemState,
    Task,
)


class MemoryStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------ companies
    def company_key(self, name: str, domain: str | None, country: str | None) -> str:
        return stable_key(
            normalize_domain(domain) if domain else name.strip().lower(), country or ""
        )

    def upsert_company(self, **fields: Any) -> tuple[Company, bool]:
        key = self.company_key(fields["name"], fields.get("domain"), fields.get("country"))
        existing = self.session.scalar(select(Company).where(Company.dedupe_key == key))
        if existing:
            for attr in ("segment", "size_indicator", "description", "city", "source"):
                value = fields.get(attr)
                if value and not getattr(existing, attr):
                    setattr(existing, attr, value)
            signals = fields.get("buying_signals") or []
            if signals:
                merged = list({*(existing.buying_signals or []), *signals})
                existing.buying_signals = merged
            self.session.flush()
            return existing, False
        company = Company(
            name=fields["name"],
            dedupe_key=key,
            domain=normalize_domain(fields["domain"]) if fields.get("domain") else None,
            country=fields.get("country"),
            city=fields.get("city"),
            segment=fields.get("segment"),
            size_indicator=fields.get("size_indicator"),
            description=fields.get("description", ""),
            buying_signals=fields.get("buying_signals") or [],
            source=fields.get("source"),
        )
        self.session.add(company)
        self.session.flush()
        return company, True

    def upsert_contact(self, company_id: str, **fields: Any) -> Contact:
        email = (fields.get("email") or "").strip().lower() or None
        existing = None
        if email:
            existing = self.session.scalar(select(Contact).where(Contact.email == email))
        if existing:
            return existing
        contact = Contact(
            company_id=company_id,
            full_name=fields["full_name"],
            role=fields.get("role"),
            email=email,
            phone=fields.get("phone"),
            confidence=float(fields.get("confidence", 0.0)),
            verified=bool(fields.get("verified", False)),
            evidence_id=fields.get("evidence_id"),
            source=fields.get("source"),
        )
        self.session.add(contact)
        self.session.flush()
        return contact

    def opt_out_contact(self, contact_id: str, reason: str = "unsubscribe") -> None:
        contact = self.session.get(Contact, contact_id)
        if contact is None:
            return
        contact.opted_out = True
        contact.opted_out_at = utcnow()
        company = self.session.get(Company, contact.company_id)
        if company is not None and reason == "complaint":
            company.opted_out = True
        self.session.flush()

    # ------------------------------------------------------------- suppliers
    def upsert_supplier(self, **fields: Any) -> Supplier:
        key = stable_key(fields["name"], fields.get("country") or "")
        existing = self.session.scalar(select(Supplier).where(Supplier.dedupe_key == key))
        if existing:
            return existing
        supplier = Supplier(
            name=fields["name"],
            dedupe_key=key,
            country=fields.get("country"),
            categories=fields.get("categories") or [],
            reliability_score=float(fields.get("reliability_score", 0.5)),
            lead_time_days=fields.get("lead_time_days"),
            payment_terms=fields.get("payment_terms"),
            documents=fields.get("documents") or [],
        )
        self.session.add(supplier)
        self.session.flush()
        return supplier

    def add_product(self, **fields: Any) -> Product:
        product = Product(**fields)
        self.session.add(product)
        self.session.flush()
        return product

    # --------------------------------------------------------- opportunities
    def create_opportunity(
        self, company_id: str, product_category: str, objective_id: str | None = None, **fields: Any
    ) -> tuple[Opportunity, bool]:
        key = stable_key(company_id, product_category, objective_id or "")
        existing = self.session.scalar(select(Opportunity).where(Opportunity.dedupe_key == key))
        if existing:
            return existing, False
        opp = Opportunity(
            company_id=company_id,
            product_category=product_category,
            objective_id=objective_id,
            dedupe_key=key,
            stage=OpportunityStage.DISCOVERED.value,
            **fields,
        )
        self.session.add(opp)
        self.session.flush()
        self.session.add(
            StageTransition(
                opportunity_id=opp.id, from_stage=None, to_stage=opp.stage, reason="discovered"
            )
        )
        self.session.flush()
        return opp, True

    def transition(
        self, opportunity: Opportunity, stage: OpportunityStage, reason: str = "", actor: str = "system"
    ) -> None:
        if opportunity.stage == stage.value:
            return
        self.session.add(
            StageTransition(
                opportunity_id=opportunity.id,
                from_stage=opportunity.stage,
                to_stage=stage.value,
                reason=reason[:2000],
                actor=actor,
            )
        )
        opportunity.stage = stage.value
        if stage in (OpportunityStage.WON, OpportunityStage.LOST, OpportunityStage.STALE):
            opportunity.closed_at = utcnow()
        self.session.flush()

    # ------------------------------------------------------------- evidence
    def record_evidence(self, record: EvidenceRecord) -> Evidence:
        row = Evidence(
            claim=record.claim[:8000],
            kind=record.kind.value,
            source=record.source[:500],
            source_type=record.source_type,
            retrieved_at=record.retrieved_at or utcnow(),
            confidence=record.confidence,
            verification=record.verification.value,
            subject_type=record.subject_type,
            subject_id=record.subject_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def evidence_for(self, subject_type: str, subject_id: str) -> list[Evidence]:
        return list(
            self.session.scalars(
                select(Evidence).where(
                    Evidence.subject_type == subject_type, Evidence.subject_id == subject_id
                )
            )
        )

    def record_compliance(self, **fields: Any) -> ComplianceEvent:
        event = ComplianceEvent(**fields)
        self.session.add(event)
        self.session.flush()
        return event

    # ---------------------------------------------------------------- state
    def control_state(self) -> dict:
        row = self.session.get(SystemState, "control")
        return dict(row.value) if row else {}

    def set_control_state(self, **updates: Any) -> dict:
        row = self.session.get(SystemState, "control")
        if row is None:
            row = SystemState(key="control", value={})
            self.session.add(row)
        value = dict(row.value or {})
        value.update(updates)
        row.value = value
        self.session.flush()
        return value


class TaskStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_objective(self, title: str, description: str = "", **fields: Any) -> Objective:
        obj = Objective(title=title, description=description, **fields)
        self.session.add(obj)
        self.session.flush()
        return obj

    def create_task(
        self,
        agent: str,
        objective_id: str | None = None,
        task_input: dict | None = None,
        priority: int = 50,
        parent_id: str | None = None,
        idempotency_key: str | None = None,
        scheduled_for: datetime | None = None,
    ) -> tuple[Task, bool]:
        if idempotency_key:
            existing = self.session.scalar(
                select(Task).where(Task.idempotency_key == idempotency_key)
            )
            if existing:
                return existing, False
        task = Task(
            agent=agent,
            objective_id=objective_id,
            input=task_input or {},
            priority=priority,
            parent_id=parent_id,
            idempotency_key=idempotency_key,
            scheduled_for=scheduled_for,
        )
        self.session.add(task)
        self.session.flush()
        return task, True

    def next_pending(self, objective_id: str | None = None, now: datetime | None = None) -> Task | None:
        moment = now or utcnow()
        # Work for a paused or closed objective waits; work with no objective
        # (inbound replies) always runs.
        stmt = (
            select(Task)
            .outerjoin(Objective, Task.objective_id == Objective.id)
            .where(Task.status == "pending", or_(Task.objective_id.is_(None), Objective.status == "active"))
        )
        if objective_id:
            stmt = stmt.where(Task.objective_id == objective_id)
        stmt = stmt.order_by(Task.priority.desc(), Task.created_at.asc())
        for task in self.session.scalars(stmt):
            if task.scheduled_for and task.scheduled_for > moment:
                continue
            return task
        return None

    def pending_count(self, objective_id: str | None = None) -> int:
        stmt = select(Task).where(Task.status.in_(("pending", "running")))
        if objective_id:
            stmt = stmt.where(Task.objective_id == objective_id)
        return len(list(self.session.scalars(stmt)))

    def mark_running(self, task: Task) -> None:
        task.status = "running"
        task.attempts += 1
        task.started_at = utcnow()
        self.session.flush()

    def mark_done(self, task: Task, output: dict) -> None:
        task.status = "done"
        task.output = output
        task.finished_at = utcnow()
        self.session.flush()

    def mark_failed(self, task: Task, error: str, retry: bool = True) -> None:
        task.error = error[:4000]
        if retry and task.attempts < task.max_attempts:
            task.status = "pending"
        else:
            task.status = "failed"
            task.finished_at = utcnow()
        self.session.flush()

    def mark_blocked(self, task: Task, reason: str, escalated: bool = False) -> None:
        task.status = "escalated" if escalated else "blocked"
        task.error = reason[:4000]
        task.finished_at = utcnow()
        self.session.flush()

    def recover_stale_running(self) -> int:
        """After a restart, running tasks are returned to the queue."""
        stale = list(self.session.scalars(select(Task).where(Task.status == "running")))
        for task in stale:
            task.status = "pending"
            task.started_at = None
        self.session.flush()
        return len(stale)
