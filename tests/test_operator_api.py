"""Operator surface: login, reviews, outcomes, catalogue, settings, unsubscribe, notifications, CLI."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, reset_settings_cache
from app.database.models import Notification
from app.database.session import reset_engine
from app.notify import notify


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    clients = []

    def factory(**env):
        monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/op-{len(clients)}.sqlite3")
        monkeypatch.setenv("NEXUS_MODE", "simulation")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        reset_settings_cache()
        reset_engine()
        from app.api.main import app

        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)
    reset_engine()
    reset_settings_cache()


def _auth(user="francis", password="correct horse"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_login_is_required_when_configured(make_client):
    client = make_client(DASHBOARD_USERNAME="francis", DASHBOARD_PASSWORD="correct horse")
    assert client.get("/health").status_code == 200  # public
    assert client.get("/api/state").status_code == 401
    assert client.get("/", headers=_auth(password="wrong")).status_code == 401
    assert client.get("/api/state", headers=_auth()).status_code == 200


def test_production_refuses_without_a_login(make_client):
    client = make_client(NEXUS_ENV="production")
    assert client.get("/api/state").status_code == 503
    assert client.get("/health").json()["production_ready"] is False


def test_review_decision_round_trip(make_client):
    client = make_client()
    from app.core.context import build_context
    from app.database.session import session_scope

    with session_scope() as session:
        ctx = build_context(session)
        ctx.audit.record("human_handoff_required", summary="rfq reply needs a quotation", decision="escalate")
    items = client.get("/api/reviews").json()
    assert len(items) == 1 and items[0]["kind"] == "commercial_handoff"
    decided = client.post(f"/api/reviews/{items[0]['id']}/decision", json={"decision": "resolve", "note": "quoted by phone"})
    assert decided.status_code == 200 and decided.json()["status"] == "resolved"
    assert client.get("/api/reviews").json() == []
    again = client.post(f"/api/reviews/{items[0]['id']}/decision", json={"decision": "resolve"})
    assert again.status_code == 422
    assert client.get("/api/state").json()["reviews"] == {"resolved": 1}


def test_catalogue_settings_and_outcomes_over_http(make_client):
    client = make_client()
    template = client.get("/api/catalogue/template/prices").json()["csv"]
    csv_text = template + "refurbished_laptop,,grade A,Kenya,300,340,reseller survey,field notes 2026-09,2026-12-31\n"
    result = client.post("/api/catalogue/import?kind=prices", content=csv_text, headers={"content-type": "text/csv"}).json()
    assert result["imported"] == 1
    assert client.get("/api/catalogue/prices").json()[0]["country"] == "Kenya"

    bad = client.post("/api/catalogue/offers", json={"product_category": "refurbished_laptop", "product_name": "x",
                                                     "supplier_name": "y", "unit_cost_low_usd": 10})
    assert bad.status_code == 422 and "source" in bad.json()["detail"]

    saved = client.put("/api/settings/commercial", json={"min_margin_pct": {"pharmaceutical": 20}})
    assert saved.status_code == 200 and saved.json()["min_margin_pct"]["pharmaceutical"] == 20
    assert client.put("/api/settings/commercial", json={"min_margin_pct": {"pharmaceutical": 250}}).status_code == 422

    assert client.post("/api/opportunities/opp_missing/outcome", json={"result": "won"}).status_code == 422


def test_unsubscribe_link_confirms_then_suppresses(make_client):
    client = make_client(UNSUBSCRIBE_SECRET="s" * 32)
    from app.database.models import Contact
    from app.database.session import session_scope
    from app.execution.email import unsubscribe_token
    from app.core.context import build_context

    with session_scope() as session:
        ctx = build_context(session)
        company, _ = ctx.memory.upsert_company(name="Link Co", domain="link.example", country="Kenya")
        contact = ctx.memory.upsert_contact(company_id=company.id, full_name="Link Person", role="Buyer",
                                            email="p@link.example", confidence=0.7, source="site")
        contact_id = contact.id
    token = unsubscribe_token(Settings(_env_file=None, unsubscribe_secret="s" * 32), contact_id)
    page = client.get(f"/u/{token}")
    assert page.status_code == 200 and "<form" in page.text
    with session_scope() as session:
        assert session.get(Contact, contact_id).opted_out is False  # viewing alone changes nothing
    assert client.post(f"/u/{token}").status_code == 200
    with session_scope() as session:
        assert session.get(Contact, contact_id).opted_out is True
    assert client.post("/u/forged.token").status_code == 404


def test_dashboard_escapes_web_content(make_client):
    body = make_client().get("/").text
    assert "NEXUS control" in body and "const esc" in body


# ------------------------------------------------------------------ notifications


def test_notifications_dedupe_and_rate_limit(ctx, monkeypatch):
    sent = []
    monkeypatch.setitem(__import__("app.notify", fromlist=["SENDERS"]).SENDERS, "telegram",
                        lambda settings, subject, body: sent.append(subject))
    ctx.settings = Settings(_env_file=None, notify_channels="telegram", notify_max_per_hour=2)
    assert notify(ctx, "a", "x", dedupe_key="k1") == ["telegram"]
    assert notify(ctx, "a", "x", dedupe_key="k1") == []  # duplicate
    notify(ctx, "b", "x")
    assert notify(ctx, "c", "x") == []  # third in the hour is suppressed
    assert sent == ["a", "b"]
    statuses = [n.status for n in ctx.session.scalars(select(Notification).order_by(Notification.created_at))]
    assert statuses.count("suppressed") == 1


def test_failed_channel_never_raises(ctx, monkeypatch):
    def boom(settings, subject, body):
        raise RuntimeError("telegram down")

    monkeypatch.setitem(__import__("app.notify", fromlist=["SENDERS"]).SENDERS, "telegram", boom)
    ctx.settings = Settings(_env_file=None, notify_channels="log,telegram")
    assert notify(ctx, "subject", "body") == ["log"]


def test_new_reviews_are_announced_once(ctx):
    from app.scheduler.jobs import notify_reviews

    ctx.audit.record("compliance_escalation", summary="needs a look", decision="escalate")
    assert notify_reviews(ctx)["items"] == 1
    assert notify_reviews(ctx)["items"] == 0


# ------------------------------------------------------------------ CLI


def test_cli_doctor_settings_and_catalogue(tmp_path, capsys, monkeypatch):
    from app.cli import main

    url = f"sqlite+pysqlite:///{tmp_path}/cli.sqlite3"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings_cache()
    reset_engine()
    try:
        assert main(["--database-url", url, "doctor"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["checks"]["database"] == "ok" and report["missing"]

        assert main(["--database-url", url, "settings", "set", "min_margin_pct", '{"used_iphone": 9}']) == 0
        assert json.loads(capsys.readouterr().out)["used_iphone"] == 9
        assert main(["--database-url", url, "settings", "set", "nonsense", "1"]) == 2
        capsys.readouterr()

        assert main(["catalogue", "template", "offers"]) == 0
        header = capsys.readouterr().out
        csv_file = tmp_path / "offers.csv"
        csv_file.write_text(header + "Acme,UAE,used_iphone,iPhone 13 128GB,grade A,100,,280,300,FOB Dubai,,10,T/T,,6 months,2026-12-31,quote A-1,,\n")
        assert main(["--database-url", url, "catalogue", "import", "offers", str(csv_file)]) == 0
        assert json.loads(capsys.readouterr().out)["imported"] == 1
        assert main(["--database-url", url, "catalogue", "list", "offers"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["product_name"] == "iPhone 13 128GB"

        assert main(["--database-url", url, "review", "list"]) == 0
        assert json.loads(capsys.readouterr().out) == []
    finally:
        reset_engine()
        reset_settings_cache()
