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


_production = get_settings().nexus_env == "production"
# Interactive API docs would bypass the login, so they exist only in development.
app = FastAPI(
    title="NEXUS AI", version="0.2.0", lifespan=lifespan,
    docs_url=None if _production else "/docs", redoc_url=None,
    openapi_url=None if _production else "/openapi.json",
)
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


class ObjectiveStatusIn(BaseModel):
    status: str


class SupplierStatusIn(BaseModel):
    status: str
    note: str = ""


class SupplierResearchIn(BaseModel):
    product_category: str
    regions: list[str] = Field(default_factory=list)


class DecisionIn(BaseModel):
    decision: str
    note: str = ""
    subject: str | None = None  # edited draft (approve only)
    body: str | None = None
    body_english: str | None = None  # edited English version of a draft in another language


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
            "suppliers": {
                status or "none": count
                for status, count in session.execute(
                    select(Company.status, func.count()).where(Company.kind == "supplier").group_by(Company.status)
                ).all()
            },
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


@app.post("/api/objectives/{objective_id}/status")
def set_objective_status(objective_id: str, payload: ObjectiveStatusIn, actor: str = Operator) -> dict:
    allowed = {"active", "paused", "completed", "abandoned"}
    if payload.status not in allowed:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(allowed)}")
    with session_scope() as session:
        ctx = build_context(session)
        objective = session.get(Objective, objective_id)
        if objective is None:
            raise HTTPException(status_code=404, detail="no such objective")
        objective.status = payload.status
        ctx.audit.record("objective_status", summary=f"{objective.title}: {payload.status}", decision="allow", actor=actor)
        return {"id": objective.id, "status": objective.status}


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
            item = queue.decide(ctx, review_id, payload.decision, payload.note, actor=actor,
                                subject=payload.subject, body=payload.body, body_english=payload.body_english)
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
                "contact": {"name": contact.full_name, "email": contact.email, "source": contact.source,
                            "role": contact.role, "linkedin_url": contact.linkedin_url} if contact else None,
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


# ------------------------------------------------------------------ suppliers


@app.get("/api/suppliers")
def list_suppliers(status: str | None = None, _: str = Operator) -> list[dict]:
    from app.database.models import SupplierRFQ

    with session_scope() as session:
        query = select(Company).where(Company.kind == "supplier").order_by(desc(Company.score)).limit(500)
        if status:
            query = query.where(Company.status == status)
        rows = []
        for company in session.scalars(query):
            contact = session.scalar(
                select(Contact).where(Contact.company_id == company.id).order_by(desc(Contact.confidence))
            )
            rfq = session.scalar(
                select(SupplierRFQ).where(SupplierRFQ.company_id == company.id).order_by(desc(SupplierRFQ.created_at))
            )
            profile = company.profile or {}
            rows.append({
                "id": company.id, "name": company.name, "country": company.country, "website": company.domain,
                "status": company.status, "score": company.score, "type": company.segment,
                "categories": profile.get("categories", []),
                "certifications": profile.get("certifications", []),
                "red_flags": profile.get("red_flags", []),
                "products": profile.get("products", [])[:3],
                "contact": {"name": contact.full_name, "email": contact.email, "opted_out": contact.opted_out} if contact else None,
                "rfq": {"status": rfq.status, "category": rfq.product_category, "followups": rfq.followups_sent,
                        "sent_at": rfq.last_sent_at.isoformat() if rfq.last_sent_at else None} if rfq else None,
                "source": company.source,
            })
        return rows


@app.post("/api/suppliers/{company_id}/status")
def set_supplier_status(company_id: str, payload: SupplierStatusIn, actor: str = Operator) -> dict:
    allowed = {"blocked", "qualified", "lead", "approved"}
    if payload.status not in allowed:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(allowed)}")
    with session_scope() as session:
        ctx = build_context(session)
        company = session.get(Company, company_id)
        if company is None or company.kind != "supplier":
            raise HTTPException(status_code=404, detail="no such supplier")
        company.status = payload.status
        if payload.status == "blocked":
            company.opted_out = True  # never contacted again
        queued = 0
        if payload.status == "qualified":
            company.opted_out = False
            for category in (company.profile or {}).get("categories", []):
                _, created = ctx.tasks.create_task(
                    agent="supplier_rfq", objective_id=None,
                    task_input={"company_id": company.id, "product_category": category}, priority=70,
                )
                queued += int(created)
        ctx.audit.record("supplier_status", summary=f"{company.name}: {payload.status}", decision="allow",
                         actor=actor, note=payload.note)
        return {"id": company.id, "status": company.status, "rfqs_queued": queued}


@app.post("/api/suppliers/research")
def start_supplier_research(payload: SupplierResearchIn, actor: str = Operator) -> dict:
    if payload.product_category not in {c.value for c in ProductCategory}:
        raise HTTPException(status_code=422, detail="unknown product category")
    with session_scope() as session:
        ctx = build_context(session)
        task, created = ctx.tasks.create_task(
            agent="supplier_research", objective_id=None,
            task_input={"product_category": payload.product_category, "regions": payload.regions,
                        "requested_at": ctx.now.isoformat()},
            priority=80,
        )
        ctx.audit.record("supplier_research_requested", summary=payload.product_category, decision="allow", actor=actor)
        return {"task_id": task.id, "queued": created}


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



# ------------------------------------------------------------------ public site (landing pages, enquiries)
# No login: these pages are what ads send people to. Nothing here reveals
# operator data; enquiries are validated, rate-limited and spam-trapped.

def _site_params(request: Request) -> dict[str, str]:
    from app.site.render import ATTRIBUTION_PARAMS

    return {k: request.query_params.get(k, "")[:300] for k in ATTRIBUTION_PARAMS}


def _visitor(request: Request, settings: Any) -> str:
    from app.site.leads import visitor_hash

    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = forwarded or (request.client.host if request.client else "")
    from datetime import date

    return visitor_hash(settings, ip, request.headers.get("user-agent", ""), date.today().isoformat())


def _published(session, slug: str):
    from app.database.models import LandingPage

    page = session.scalar(select(LandingPage).where(LandingPage.slug == slug))
    if page is None or page.status != "published":
        raise HTTPException(status_code=404, detail="page not found")
    return page


def _other_language(session, page) -> tuple[str, str] | None:
    """Link to the same page in another language, if one is published."""
    from app.core.languages import LANGUAGES
    from app.database.models import LandingPage

    sibling = session.scalar(select(LandingPage).where(
        LandingPage.product_category == page.product_category, LandingPage.country == page.country,
        LandingPage.status == "published", LandingPage.language != page.language))
    if sibling is None:
        return None
    return f"/p/{sibling.slug}", LANGUAGES.get(sibling.language, LANGUAGES["en"])["native"]


def _wa_route(slug: str, params: dict[str, str]) -> str | None:
    from urllib.parse import urlencode

    if not get_settings().whatsapp_number:
        return None
    query = urlencode({k: v for k, v in params.items() if v})
    return f"/p/{slug}/wa" + (f"?{query}" if query else "")


@app.get("/site", response_class=HTMLResponse)
def site_index() -> str:
    from app.database.models import LandingPage
    from app.site.render import render_index

    with session_scope() as session:
        pages = list(session.scalars(select(LandingPage).where(LandingPage.status == "published").order_by(LandingPage.slug)))
        return render_index(pages, get_settings())


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> str:
    from app.site.render import render_privacy

    return render_privacy(get_settings())


@app.get("/p/{slug}", response_class=HTMLResponse)
def landing_page(slug: str, request: Request) -> str:
    from app.site.leads import is_bot, record_event
    from app.site.render import render_landing

    params = _site_params(request)
    with session_scope() as session:
        page = _published(session, slug)
        if not is_bot(request.headers.get("user-agent", "")):
            record_event(session, page, "view", params, _visitor(request, get_settings()))
        return render_landing(page, params, _wa_route(slug, params), _other_language(session, page))


@app.get("/p/{slug}/wa")
def landing_whatsapp(slug: str, request: Request):
    from fastapi.responses import RedirectResponse

    from app.site.leads import is_bot, record_event
    from app.site.render import whatsapp_link

    settings = get_settings()
    with session_scope() as session:
        page = _published(session, slug)
        link = whatsapp_link(settings, page)
        if link is None:
            raise HTTPException(status_code=404, detail="WhatsApp is not set up")
        if not is_bot(request.headers.get("user-agent", "")):
            record_event(session, page, "whatsapp", _site_params(request), _visitor(request, settings))
        return RedirectResponse(link, status_code=302)


@app.post("/p/{slug}/enquiry", response_class=HTMLResponse)
async def landing_enquiry(slug: str, request: Request):
    from urllib.parse import parse_qs

    from app.site.leads import LeadRejected, intake
    from app.site.render import render_landing, render_thanks

    raw = await request.body()
    if len(raw) > 20_000:
        raise HTTPException(status_code=413, detail="enquiry too large")
    form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True).items()}
    settings = get_settings()
    with session_scope() as session:
        page = _published(session, slug)
        ctx = build_context(session)
        params = {k: form.get(k, "") for k in _site_params(request)}
        try:
            intake(ctx, page, form, _visitor(request, settings))
        except LeadRejected as exc:
            html = render_landing(page, params, _wa_route(slug, params), _other_language(session, page), error=str(exc))
            return HTMLResponse(html, status_code=422)
        return HTMLResponse(render_thanks(page, _wa_route(slug, params)))


def _esc(text: str) -> str:
    from html import escape

    return escape(text)


# ------------------------------------------------------------------ ads, leads, learning (operator)


class CampaignActionIn(BaseModel):
    action: str  # pause | resume | budget | remove
    daily_budget_usd: float | None = None


class PlanAdsIn(BaseModel):
    platform: str | None = None
    product_category: str | None = None
    country: str | None = None


def _campaign_row(session, c) -> dict:
    from app.database.models import AdVariant
    from app.learning.signals import campaign_totals, variant_stats

    t = campaign_totals(session, c.id)
    stats = variant_stats(session, c.id)
    return {
        "id": c.id, "platform": c.platform, "name": c.name, "category": c.product_category, "countries": c.countries,
        "language": c.language, "status": c.status, "status_reason": c.status_reason, "daily_budget_usd": c.daily_budget_usd,
        "launched_at": c.launched_at.isoformat() if c.launched_at else None, **t,
        "ctr": round(t["clicks"] / t["impressions"], 4) if t["impressions"] else None,
        "cost_per_lead": round(t["spend_usd"] / t["leads"], 2) if t["leads"] else None,
        "lead_rate": round(t["leads"] / t["clicks"], 4) if t["clicks"] else None,
        "plan": c.plan, "targeting": c.targeting,
        "variants": [{"key": v.key, "angle": v.angle, "status": v.status, "status_reason": v.status_reason,
                      "content": v.content, "asset_id": v.asset_id, **stats.get(v.key, {})}
                     for v in session.scalars(select(AdVariant).where(AdVariant.campaign_id == c.id))],
    }


@app.get("/api/ads/campaigns")
def list_campaigns(_: str = Operator) -> list[dict]:
    from app.database.models import AdCampaign

    with session_scope() as session:
        return [_campaign_row(session, c) for c in session.scalars(select(AdCampaign).order_by(desc(AdCampaign.created_at)))]


@app.post("/api/ads/plan")
def plan_ads(payload: PlanAdsIn, actor: str = Operator) -> dict:
    from app.core.ids import stable_key

    with session_scope() as session:
        ctx = build_context(session)
        task, created = ctx.tasks.create_task(
            agent="ad_planner", objective_id=None, task_input=payload.model_dump(exclude_none=True), priority=75,
            idempotency_key=stable_key("ad_planner_manual", actor, ctx.now.isoformat()))
        return {"task_id": task.id, "queued": created}


@app.post("/api/ads/campaigns/{campaign_id}")
def campaign_action(campaign_id: str, payload: CampaignActionIn, actor: str = Operator) -> dict:
    from app.ads.agents import fx_to_native
    from app.ads.platforms import AdPlatformError, platform_for
    from app.database.models import AdCampaign

    with session_scope() as session:
        ctx = build_context(session)
        c = session.get(AdCampaign, campaign_id)
        if c is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        platform = platform_for(ctx, c.platform)
        live = bool(c.external_ids) and platform is not None
        try:
            if payload.action == "pause":
                if live:
                    platform.set_status(c.external_ids, active=False)
                c.status, c.status_reason = "paused", f"paused by {actor}"
            elif payload.action == "resume":
                if c.status not in ("paused", "paused_no_leads"):
                    raise HTTPException(status_code=422, detail=f"cannot resume a campaign that is {c.status}")
                if live:
                    platform.set_status(c.external_ids, active=True)
                c.status, c.status_reason = "active", None
            elif payload.action == "budget":
                value = float(payload.daily_budget_usd or 0)
                if value < ctx.settings.ads_min_daily_budget_usd:
                    raise HTTPException(status_code=422, detail=f"minimum is {ctx.settings.ads_min_daily_budget_usd} USD/day")
                if live:
                    platform.set_budget(c.external_ids, fx_to_native(ctx, value, platform.currency))
                c.daily_budget_usd = round(value, 2)
            elif payload.action == "remove":
                if live:
                    platform.set_status(c.external_ids, active=False)
                c.status, c.status_reason = "removed", f"removed by {actor}"
            else:
                raise HTTPException(status_code=422, detail="action must be pause, resume, budget or remove")
        except AdPlatformError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        ctx.audit.record("ad_campaign_operator", summary=f"{payload.action} {c.name}", actor=actor, decision="allow",
                         campaign_id=c.id, daily_budget_usd=c.daily_budget_usd)
        return _campaign_row(session, c)


@app.get("/api/ads/assets")
def list_assets(_: str = Operator) -> list[dict]:
    from app.database.models import AdAsset

    with session_scope() as session:
        return [{"id": a.id, "category": a.product_category, "kind": a.kind, "filename": a.filename,
                 "caption": a.caption, "active": a.active, "created_at": a.created_at.isoformat()}
                for a in session.scalars(select(AdAsset).order_by(desc(AdAsset.created_at)).limit(200))]


@app.post("/api/ads/assets", status_code=201)
async def upload_asset(request: Request, category: str, caption: str = "", actor: str = Operator) -> dict:
    from app.ads.compliance import ad_allowed
    from app.ads.creative import normalise_photo
    from app.database.models import AdAsset

    if category not in {c.value for c in ProductCategory} or not ad_allowed(category):
        raise HTTPException(status_code=422, detail="unknown product line, or one that is never advertised")
    raw = await request.body()
    if len(raw) > 8_000_000:
        raise HTTPException(status_code=413, detail="image over 8 MB")
    try:
        data, content_type = normalise_photo(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with session_scope() as session:
        asset = AdAsset(product_category=category, kind="photo", filename=f"{category}.jpg", content_type=content_type,
                        data=data, caption=caption[:300])
        session.add(asset)
        session.flush()
        build_context(session).audit.record("ad_asset_uploaded", summary=f"{category} photo", actor=actor,
                                            decision="allow", asset_id=asset.id)
        return {"id": asset.id}


@app.post("/api/ads/assets/{asset_id}/deactivate")
def deactivate_asset(asset_id: str, _: str = Operator) -> dict:
    from app.database.models import AdAsset

    with session_scope() as session:
        asset = session.get(AdAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        asset.active = False
        return {"id": asset.id, "active": False}


@app.get("/a/{asset_id}")
def asset_file(asset_id: str, _: str = Operator):
    from fastapi.responses import Response

    from app.database.models import AdAsset

    with session_scope() as session:
        asset = session.get(AdAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        return Response(asset.data, media_type=asset.content_type)


@app.get("/api/pages")
def list_pages(_: str = Operator) -> list[dict]:
    from app.database.models import LandingPage, Lead, PageEvent

    settings = get_settings()
    with session_scope() as session:
        rows = []
        for p in session.scalars(select(LandingPage).order_by(LandingPage.slug)):
            events = dict(session.execute(select(PageEvent.kind, func.count()).where(PageEvent.landing_page_id == p.id)
                                          .group_by(PageEvent.kind)).all())
            leads = session.scalar(select(func.count()).select_from(Lead).where(Lead.landing_page_id == p.id, Lead.status != "spam"))
            rows.append({"slug": p.slug, "category": p.product_category, "country": p.country, "language": p.language,
                         "status": p.status,
                         "version": p.version, "headline": (p.content or {}).get("headline"),
                         "url": f"{settings.site_url}/p/{p.slug}" if settings.site_url else f"/p/{p.slug}",
                         "views": events.get("view", 0), "whatsapp": events.get("whatsapp", 0), "leads": leads,
                         "conversion": round(leads / events["view"], 4) if events.get("view") else None})
        return rows


@app.post("/api/pages")
def publish_page(payload: dict = Body(...), actor: str = Operator) -> dict:
    from app.site.pages import LandingPageAgent

    category, country = payload.get("product_category"), payload.get("country")
    if category not in {c.value for c in ProductCategory} or not country:
        raise HTTPException(status_code=422, detail="product_category and country are required")
    with session_scope() as session:
        ctx = build_context(session)
        task_input = {"product_category": category, "country": country}
        if payload.get("language"):
            task_input["language"] = payload["language"]
        result = LandingPageAgent().run(ctx, task_input)
        if not result.ok:
            raise HTTPException(status_code=422, detail=result.error)
        return result.output


@app.get("/api/leads")
def list_leads(limit: int = 200, _: str = Operator) -> list[dict]:
    from app.database.models import Lead

    with session_scope() as session:
        return [{"id": lead.id, "at": lead.created_at.isoformat(), "name": lead.full_name, "organisation": lead.organisation,
                 "email": lead.email, "phone": lead.phone, "country": lead.country, "category": lead.product_category,
                 "quantity": lead.quantity, "message": lead.message[:500], "platform": lead.platform, "campaign_id": lead.campaign_id,
                 "variant": lead.variant_key, "status": lead.status, "opportunity_id": lead.opportunity_id,
                 "conversions_sent": lead.conversions_sent}
                for lead in session.scalars(select(Lead).where(Lead.status != "spam").order_by(desc(Lead.created_at)).limit(min(limit, 1000)))]


@app.get("/api/learning")
def learning_state(refresh: bool = False, _: str = Operator) -> dict:
    from app.database.models import SystemState
    from app.learning import loop

    with session_scope() as session:
        ctx = build_context(session)
        row = session.get(SystemState, "learning_snapshot")
        if refresh or row is None:
            data = loop.snapshot(ctx)
            stored = row.value if row is not None else {}
            data.update({k: stored[k] for k in ("calibration", "rollback", "observations", "metrics") if k in stored})
            return data
        return row.value


@app.post("/api/learning/rollback")
def learning_rollback(payload: dict = Body(default={}), actor: str = Operator) -> dict:
    from app.learning import loop

    with session_scope() as session:
        ctx = build_context(session)
        if loop.active_scoring(session) is None:
            raise HTTPException(status_code=422, detail="the default scoring weights are already in use")
        restored = loop.rollback_scoring(ctx, reason=payload.get("reason") or "operator rollback", actor=actor)
        return {"restored_version": restored.version if restored else 0, "weights": loop.active_weights(session)}


# ------------------------------------------------------------------ dashboard


@app.get("/", response_class=HTMLResponse)
def dashboard(_: str = Operator) -> str:
    return DASHBOARD.read_text(encoding="utf-8")
