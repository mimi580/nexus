"""FastAPI surface: health, state, objective intake, and the control panel."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from app.agents.learning import metrics
from app.core.config import get_settings
from app.core.context import build_context
from app.core.logging import configure_logging
from app.core.types import ProductCategory
from app.database.models import (
    AuditEvent,
    ComplianceEvent,
    Objective,
    Opportunity,
    Task,
)
from app.database.session import create_all, session_scope
from app.orchestrator.orchestrator import Orchestrator
from app.policies.licenses import (
    LicenseError,
    add_license,
    as_dict as lic_as_dict,
    deactivate_license,
    list_licenses,
)
from app.scheduler.scheduler import Scheduler

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"

@asynccontextmanager
async def lifespan(_app: FastAPI):  # pragma: no cover - process lifecycle
    configure_logging()
    create_all()
    yield


app = FastAPI(title="NEXUS AI", version="0.1.0", lifespan=lifespan)


class ObjectiveIn(BaseModel):
    title: str
    product_categories: list[str] = Field(default_factory=list)
    description: str = ""


class LicenseIn(BaseModel):
    holder_name: str
    country: str
    issuing_authority: str
    license_number: str
    license_types: list[str]
    product_categories: list[str]
    expires_on: str
    valid_from: str | None = None
    scope_notes: str = ""
    document_ref: str | None = None
    verification: str = "user_provided"


class ControlIn(BaseModel):
    paused: bool | None = None
    emergency_stop: bool | None = None
    paused_categories: list[str] | None = None
    paused_geographies: list[str] | None = None


@app.get("/health")
def health() -> dict:
    with session_scope() as session:
        session.execute(select(func.count()).select_from(Objective))
    settings = get_settings()
    return {"status": "ok", "mode": settings.nexus_mode, "env": settings.nexus_env}


@app.get("/api/state")
def state() -> dict[str, Any]:
    with session_scope() as session:
        ctx = build_context(session)
        pipeline = {
            stage: count
            for stage, count in session.execute(
                select(Opportunity.stage, func.count()).group_by(Opportunity.stage)
            ).all()
        }
        tasks = {
            status: count
            for status, count in session.execute(
                select(Task.status, func.count()).group_by(Task.status)
            ).all()
        }
        blocked = [
            {
                "at": event.created_at.isoformat(),
                "type": event.event_type,
                "summary": event.summary,
                "decision": event.decision,
            }
            for event in session.scalars(
                select(AuditEvent)
                .where(AuditEvent.decision.in_(("block", "escalate")))
                .order_by(desc(AuditEvent.created_at))
                .limit(15)
            )
        ]
        errors = [
            {"agent": t.agent, "error": t.error, "at": t.updated_at.isoformat()}
            for t in session.scalars(
                select(Task).where(Task.status == "failed").order_by(desc(Task.updated_at)).limit(10)
            )
        ]
        compliance = [
            {"flag": c.flag, "severity": c.severity, "detail": c.detail, "resolved": c.resolved}
            for c in session.scalars(
                select(ComplianceEvent).order_by(desc(ComplianceEvent.created_at)).limit(10)
            )
        ]
        objectives = [
            {"id": o.id, "title": o.title, "status": o.status, "categories": (o.constraints or {}).get("product_categories", [])}
            for o in session.scalars(select(Objective).order_by(desc(Objective.created_at)).limit(10))
        ]
        return {
            "objectives": objectives,
            "pipeline": pipeline,
            "tasks": tasks,
            "budget": ctx.budget.snapshot(),
            "control": ctx.memory.control_state(),
            "blocked_actions": blocked,
            "errors": errors,
            "compliance": compliance,
            "metrics": metrics(ctx, window_days=30),
            "licenses": [lic_as_dict(lic, ctx.now.date()) for lic in list_licenses(session)],
        }


@app.post("/api/objectives")
def create_objective(payload: ObjectiveIn) -> dict:
    valid = {c.value for c in ProductCategory}
    unknown = [c for c in payload.product_categories if c not in valid]
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_categories": unknown, "valid": sorted(valid)})
    with session_scope() as session:
        ctx = build_context(session)
        objective = Orchestrator(ctx).create_objective(
            title=payload.title,
            product_categories=payload.product_categories,
            description=payload.description,
        )
        return {"id": objective.id, "title": objective.title, "status": objective.status}


@app.post("/api/run")
def run_pass(objective_id: str | None = None) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        summary = Orchestrator(ctx).run(objective_id)
        jobs = Scheduler(ctx).run_due()
        return {"loop": summary, "jobs": jobs}


@app.get("/api/licenses")
def get_licenses(include_inactive: bool = False) -> list[dict]:
    with session_scope() as session:
        ctx = build_context(session)
        return [lic_as_dict(lic, ctx.now.date()) for lic in list_licenses(session, include_inactive)]


@app.post("/api/licenses", status_code=201)
def create_license(payload: LicenseIn) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            lic = add_license(session, **payload.model_dump())
        except LicenseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ctx.audit.record(
            "license_added", summary=f"{lic.country} licence {lic.license_number}", decision="allow",
            actor="operator", license_id=lic.id,
        )
        return lic_as_dict(lic, ctx.now.date())


@app.post("/api/licenses/{license_id}/deactivate")
def deactivate(license_id: str) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            lic = deactivate_license(session, license_id)
        except LicenseError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ctx.audit.record(
            "license_deactivated", summary=f"{lic.country} licence {lic.license_number}", decision="allow",
            actor="operator", license_id=lic.id,
        )
        return lic_as_dict(lic, ctx.now.date())


@app.post("/api/control")
def control(payload: ControlIn) -> dict:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="no control fields supplied")
    with session_scope() as session:
        ctx = build_context(session)
        value = ctx.memory.set_control_state(**updates)
        ctx.audit.record("control_changed", summary=str(updates), decision="allow", **updates)
        return value


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD.read_text(encoding="utf-8")
