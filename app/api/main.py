"""FastAPI surface: health, state, operator actions and the control panel.

Everything except /health and the public unsubscribe page requires the
operator login (HTTP Basic, DASHBOARD_USERNAME / DASHBOARD_PASSWORD). In
production the API refuses to serve anything else until a login is configured.
"""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from app.agents.learning import metrics
from app.commercial import catalogue
from app.commercial import settings as commercial
from app.core.config import get_settings
from app.core.context import build_context
from app.core.logging import configure_logging
from app.core.types import ProductCategory
from app.database.models import (
    AuditEvent,
    Company,
    ComplianceEvent,
    Contact,
    Message,
    Objective,
    Opportunity,
    ReviewItem,
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
from app.review import queue
from app.scheduler.scheduler import Scheduler

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


@asynccontextmanager
async def lifespan(_app: FastAPI):  # pragma: no cover - process lifecycle
    configure_logging()
    create_all()
    yield


app = FastAPI(title="NEXUS AI", version="0.2.0", lifespan=lifespan)
basic = HTTPBasic(auto_error=False)


def require_operator(credentials: HTTPBasicCredentials | None = Depends(basic)) -> str:
    settings = get_settings()
    username, password = settings.dashboard_username, settings.dashboard_password
    if not (username and password):
        if settings.nexus_env == "production" or settings.nexus_mode == "production":
            raise HTTPException(status_code=503, detail="set DASHBOARD_USERNAME and DASHBOARD_PASSWORD")
        return "operator"  # local development without a login
    if credentials is None or not (
        secrets.compare_digest(credentials.username.encode(), username.encode())
        and secrets.compare_digest(credentials.password.encode(), password.encode())
    ):
        raise HTTPException(status_code=401, detail="login required", headers={"WWW-Authenticate": "Basic realm=NEXUS"})
    return credentials.username


Operator = Depends(require_operator)


# ------------------------------------------------------------------ schemas


class ObjectiveIn(BaseModel):
    title: str
    product_categories: list[str] = Field(default_factory=list)
    description: str = ""


class ControlIn(BaseModel):
    paused: bool | None = None
    emergency_stop: bool | None = None
    paused_categories: list[str] | None = None
    paused_geographies: list[str] | None = None


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
    regions: list[str] = Field(default_factory=list)
    coverage_countries: list[str] = Field(default_factory=list)


class DecisionIn(BaseModel):
    decision: str
    note: str = ""


class OutcomeIn(BaseModel):
    result: str
    revenue_usd: float | None = None
    margin_usd: float | None = None
    note: str = ""


# ------------------------------------------------------------------ public


@app.get("/health")
def health() -> dict:
    with session_scope() as session:
        session.execute(select(func.count()).select_from(Objective))
    settings = get_settings()
    readiness = settings.readiness()
    return {
        "status": "ok",
        "mode": settings.nexus_mode,
        "env": settings.nexus_env,
        "production_ready": not readiness["missing"],
    }


UNSUBSCRIBE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Unsubscribe</title>
<style>body{{font:16px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:12vh auto;padding:0 16px;color:#1b1f24}}
button{{font:inherit;padding:10px 18px;border-radius:6px;border:1px solid #1b1f24;background:#1b1f24;color:#fff;cursor:pointer}}</style>
</head><body>{content}</body></html>"""


def _unsubscribe(token: str) -> bool:
    from app.execution.email import verify_unsubscribe_token

    contact_id = verify_unsubscribe_token(get_settings(), token)
    if contact_id is None:
        return False
    with session_scope() as session:
        ctx = build_context(session)
        if session.get(Contact, contact_id) is None:
            return False
        ctx.memory.opt_out_contact(contact_id, reason="unsubscribe_link")
        ctx.audit.record("opt_out_recorded", summary="unsubscribe link used", decision="allow",
                         actor="recipient", contact_id=contact_id)
    return True


@app.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str) -> str:
    # GET only shows a confirmation, so link scanners cannot unsubscribe people.
    content = (
        "<h1>Unsubscribe</h1><p>Stop receiving e-mails from us?</p>"
        f"<form method='post' action='/u/{token}'><button type='submit'>Unsubscribe</button></form>"
    )
    return UNSUBSCRIBE_PAGE.format(content=content)


@app.post("/u/{token}", response_class=HTMLResponse)
def unsubscribe(token: str) -> str:
    if not _unsubscribe(token):
        raise HTTPException(status_code=404, detail="unknown or invalid link")
    return UNSUBSCRIBE_PAGE.format(content="<h1>Done</h1><p>You will not receive further e-mails from us.</p>")


# ------------------------------------------------------------------ state


@app.get("/api/state")
def state(_: str = Operator) -> dict[str, Any]:
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
            for status, count in session.execute(select(Task.status, func.count()).group_by(Task.status)).all()
        }
        blocked = [
            {"at": e.created_at.isoformat(), "type": e.event_type, "summary": e.summary, "decision": e.decision}
            for e in session.scalars(
                select(AuditEvent)
                .where(AuditEvent.decision.in_(("block", "escalate")))
                .order_by(desc(AuditEvent.created_at))
                .limit(15)
            )
        ]
        errors = [
            {"agent": t.agent, "error": t.error, "at": t.updated_at.isoformat()}
            for t in session.scalars(select(Task).where(Task.status == "failed").order_by(desc(Task.updated_at)).limit(10))
        ]
        compliance = [
            {"flag": c.flag, "severity": c.severity, "detail": c.detail, "resolved": c.resolved}
            for c in session.scalars(select(ComplianceEvent).order_by(desc(ComplianceEvent.created_at)).limit(10))
        ]
        objectives = [
            {"id": o.id, "title": o.title, "status": o.status,
             "categories": (o.constraints or {}).get("product_categories", [])}
            for o in session.scalars(select(Objective).order_by(desc(Objective.created_at)).limit(10))
        ]
        settings = ctx.settings
        return {
            "mode": settings.nexus_mode,
            "env": settings.nexus_env,
            "readiness": settings.readiness(),
            "objectives": objectives,
            "pipeline": pipeline,
            "tasks": tasks,
            "budget": ctx.budget.snapshot(),
            "control": ctx.memory.control_state(),
            "reviews": queue.counts(session),
            "blocked_actions": blocked,
            "errors": errors,
            "compliance": compliance,
            "metrics": metrics(ctx, window_days=30),
            "licenses": [lic_as_dict(lic, ctx.now.date()) for lic in list_licenses(session)],
        }


@app.get("/api/readiness")
def readiness(_: str = Operator) -> dict:
    return get_settings().readiness()


# ------------------------------------------------------------------ objectives and runs


@app.post("/api/objectives")
def create_objective(payload: ObjectiveIn, _: str = Operator) -> dict:
    valid = {c.value for c in ProductCategory}
    unknown = [c for c in payload.product_categories if c not in valid]
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_categories": unknown, "valid": sorted(valid)})
    with session_scope() as session:
        ctx = build_context(session)
        objective = Orchestrator(ctx).create_objective(
            title=payload.title, product_categories=payload.product_categories, description=payload.description,
        )
        return {"id": objective.id, "title": objective.title, "status": objective.status}


@app.post("/api/run")
def run_pass(objective_id: str | None = None, _: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        summary = Orchestrator(ctx).run(objective_id)
        jobs = Scheduler(ctx).run_due()
        return {"loop": summary, "jobs": jobs}


@app.post("/api/control")
def control(payload: ControlIn, actor: str = Operator) -> dict:
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="no control fields supplied")
    with session_scope() as session:
        ctx = build_context(session)
        value = ctx.memory.set_control_state(**updates)
        ctx.audit.record("control_changed", summary=str(updates), decision="allow", actor=actor, **updates)
        return value


# ------------------------------------------------------------------ review queue


@app.get("/api/reviews")
def list_reviews(status: str = "pending", limit: int = 100, _: str = Operator) -> list[dict]:
    with session_scope() as session:
        query = select(ReviewItem).order_by(desc(ReviewItem.created_at)).limit(min(limit, 500))
        if status != "all":
            query = query.where(ReviewItem.status == status)
        items = []
        for item in session.scalars(query):
            row = queue.as_dict(item)
            if item.opportunity_id:
                opportunity = session.get(Opportunity, item.opportunity_id)
                company = session.get(Company, opportunity.company_id) if opportunity else None
                row["opportunity"] = {
                    "company": company.name if company else None,
                    "country": company.country if company else None,
                    "category": opportunity.product_category if opportunity else None,
                    "stage": opportunity.stage if opportunity else None,
                    "economics": opportunity.economics if opportunity else None,
                }
                last_reply = session.scalar(
                    select(Message)
                    .where(Message.opportunity_id == item.opportunity_id, Message.direction == "inbound")
                    .order_by(desc(Message.created_at))
                )
                if last_reply is not None:
                    row["last_reply"] = {"subject": last_reply.subject, "body": last_reply.body[:3000]}
            items.append(row)
        return items


@app.post("/api/reviews/{review_id}/decision")
def decide_review(review_id: str, payload: DecisionIn, actor: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            item = queue.decide(ctx, review_id, payload.decision, payload.note, actor=actor)
        except queue.ReviewError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return queue.as_dict(item)


# ------------------------------------------------------------------ opportunities


@app.get("/api/opportunities")
def list_opportunities(stage: str | None = None, limit: int = 100, _: str = Operator) -> list[dict]:
    with session_scope() as session:
        query = select(Opportunity).order_by(desc(Opportunity.score)).limit(min(limit, 500))
        if stage:
            query = query.where(Opportunity.stage == stage)
        rows = []
        for opp in session.scalars(query):
            company = session.get(Company, opp.company_id)
            contact = session.get(Contact, opp.contact_id) if opp.contact_id else None
            rows.append({
                "id": opp.id,
                "company": company.name if company else None,
                "country": company.country if company else None,
                "category": opp.product_category,
                "stage": opp.stage,
                "score": round(opp.score or 0.0, 3),
                "contact": {"name": contact.full_name, "email": contact.email, "source": contact.source} if contact else None,
                "estimated_value_usd": opp.estimated_value_usd,
                "estimated_margin_usd": opp.estimated_margin_usd,
                "economics": opp.economics,
                "blocked_reason": opp.blocked_reason,
                "compliance_flags": opp.compliance_flags,
            })
        return rows


@app.post("/api/opportunities/{opportunity_id}/outcome")
def report_outcome(opportunity_id: str, payload: OutcomeIn, actor: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            outcome = queue.record_outcome(
                ctx, opportunity_id, payload.result, revenue_usd=payload.revenue_usd,
                margin_usd=payload.margin_usd, note=payload.note, actor=actor,
            )
        except queue.ReviewError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": outcome.id, "result": outcome.result}


# ------------------------------------------------------------------ catalogue and settings


@app.get("/api/catalogue/offers")
def get_offers(category: str | None = None, _: str = Operator) -> list[dict]:
    with session_scope() as session:
        return [catalogue.offer_as_dict(o, session) for o in catalogue.list_offers(session, category)]


@app.post("/api/catalogue/offers", status_code=201)
def create_offer(payload: dict = Body(...), actor: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            offer = catalogue.add_offer(session, **payload)
        except (catalogue.CatalogueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ctx.audit.record("offer_added", summary=f"{offer.product_name}", decision="allow", actor=actor, offer_id=offer.id)
        return catalogue.offer_as_dict(offer, session)


@app.post("/api/catalogue/offers/{offer_id}/deactivate")
def retire_offer(offer_id: str, _: str = Operator) -> dict:
    with session_scope() as session:
        try:
            return catalogue.offer_as_dict(catalogue.deactivate_offer(session, offer_id), session)
        except catalogue.CatalogueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/catalogue/prices")
def get_prices(category: str | None = None, _: str = Operator) -> list[dict]:
    with session_scope() as session:
        return [catalogue.price_as_dict(p) for p in catalogue.list_price_references(session, category)]


@app.post("/api/catalogue/prices", status_code=201)
def create_price(payload: dict = Body(...), actor: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            row = catalogue.add_price_reference(session, **payload)
        except (catalogue.CatalogueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ctx.audit.record("price_added", summary=f"{row.product_category} {row.country or 'all'}", decision="allow",
                         actor=actor, price_id=row.id)
        return catalogue.price_as_dict(row)


@app.post("/api/catalogue/prices/{price_id}/deactivate")
def retire_price(price_id: str, _: str = Operator) -> dict:
    with session_scope() as session:
        try:
            return catalogue.price_as_dict(catalogue.deactivate_price_reference(session, price_id))
        except catalogue.CatalogueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/catalogue/import")
async def import_catalogue(request: Request, kind: str, actor: str = Operator) -> dict:
    text = (await request.body()).decode("utf-8-sig", errors="replace")
    with session_scope() as session:
        ctx = build_context(session)
        try:
            result = catalogue.import_csv(session, text, kind)
        except catalogue.CatalogueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result["imported"]:
            ctx.audit.record("catalogue_import", summary=f"{result['imported']} {kind}", decision="allow", actor=actor)
        return result


@app.get("/api/catalogue/template/{kind}")
def catalogue_template(kind: str, _: str = Operator) -> dict:
    if kind not in {"offers", "prices"}:
        raise HTTPException(status_code=404, detail="kind is offers or prices")
    return {"csv": catalogue.template(kind)}


@app.get("/api/settings/commercial")
def get_commercial(_: str = Operator) -> dict:
    with session_scope() as session:
        return commercial.get(session)


@app.put("/api/settings/commercial")
def put_commercial(changes: dict = Body(...), actor: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            value = commercial.update(session, changes)
        except (commercial.CommercialSettingsError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ctx.audit.record("commercial_settings_changed", summary=", ".join(changes), decision="allow", actor=actor)
        return value


# ------------------------------------------------------------------ licences


@app.get("/api/licenses")
def get_licenses(include_inactive: bool = False, _: str = Operator) -> list[dict]:
    with session_scope() as session:
        ctx = build_context(session)
        return [lic_as_dict(lic, ctx.now.date()) for lic in list_licenses(session, include_inactive)]


@app.post("/api/licenses", status_code=201)
def create_license(payload: LicenseIn, actor: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            lic = add_license(session, **payload.model_dump())
        except LicenseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ctx.audit.record("license_added", summary=f"{lic.country} licence {lic.license_number}",
                         decision="allow", actor=actor, license_id=lic.id)
        return lic_as_dict(lic, ctx.now.date())


@app.post("/api/licenses/{license_id}/deactivate")
def deactivate(license_id: str, actor: str = Operator) -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        try:
            lic = deactivate_license(session, license_id)
        except LicenseError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        ctx.audit.record("license_deactivated", summary=f"{lic.country} licence {lic.license_number}",
                         decision="allow", actor=actor, license_id=lic.id)
        return lic_as_dict(lic, ctx.now.date())


# ------------------------------------------------------------------ dashboard


@app.get("/", response_class=HTMLResponse)
def dashboard(_: str = Operator) -> str:
    return DASHBOARD.read_text(encoding="utf-8")
