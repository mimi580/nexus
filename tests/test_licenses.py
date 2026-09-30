"""Licence register: validation, coverage, policy gating, copy, expiry alerts, API, CLI."""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from app.agents.outreach import OutreachAgent
from app.core.interfaces import ActionRequest
from app.core.types import ActionKind, Decision, OpportunityStage, ProductCategory
from app.database.models import ComplianceEvent, Evidence, Message
from app.policies.fact_check import validate_message
from app.policies.licenses import LicenseError, add_license, coverage, deactivate_license
from app.scheduler.jobs import license_expiry_check

PHARMA = ProductCategory.PHARMA.value
MEDICAL = ProductCategory.MEDICAL.value


def _license(session, **overrides):
    fields = dict(
        holder_name="Leonard Medical Supplies Ltd",
        country="Kenya",
        issuing_authority="Pharmacy and Poisons Board",
        license_number="PPB-TEST-001",
        license_types=["importer", "distributor"],
        product_categories=[MEDICAL, PHARMA],
        valid_from="2026-01-01",
        expires_on="2027-06-30",
    )
    fields.update(overrides)
    return add_license(session, **fields)


def _regulated_request(ctx, kind=ActionKind.SEND_OUTREACH, country="Kenya", category=PHARMA, key="lic-1"):
    company, _ = ctx.memory.upsert_company(name=f"Buyer {key}", domain=f"{key}.example", country=country)
    contact = ctx.memory.upsert_contact(
        company_id=company.id, full_name="Test Buyer", role="Procurement",
        email=f"buyer@{key}.example", confidence=0.7, source="directory",
    )
    return ActionRequest(
        kind=kind,
        summary="regulated action",
        payload={
            "contact_id": contact.id,
            "company_id": company.id,
            "product_category": category,
            "country": country,
            "subject": "Supply enquiry",
            "body": "Hello, we can quote against your requirement. Reply unsubscribe to opt out.",
            "allowed_facts": {"numbers": []},
            "personalized": True,
            "regulatory_checked": True,
            "amount_usd": 5000,
        },
        estimated_cost_usd=0.01,
        cost_category="email",
        idempotency_key=key,
    )


# ------------------------------------------------------------------ register


def test_licence_requires_an_expiry(session):
    with pytest.raises(LicenseError, match="expires_on"):
        _license(session, expires_on=None)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"product_categories": ["spaceships"]}, "unknown product categories"),
        ({"license_types": ["smuggler"]}, "unknown licence types"),
        ({"license_types": []}, "licence type"),
        ({"valid_from": "2028-01-01"}, "after expires_on"),
        ({"expires_on": "30/06/2027"}, "ISO date"),
        ({"issuing_authority": " "}, "issuing_authority"),
    ],
)
def test_invalid_licences_are_rejected(session, overrides, message):
    with pytest.raises(LicenseError, match=message):
        _license(session, **overrides)


def test_duplicate_licence_is_rejected(session):
    _license(session)
    with pytest.raises(LicenseError, match="already registered"):
        _license(session)


def test_licence_is_recorded_as_user_provided_evidence(session):
    lic = _license(session)
    evidence = session.scalars(select(Evidence).where(Evidence.subject_id == lic.id)).all()
    assert len(evidence) == 1
    assert evidence[0].kind == "user_provided"
    assert "PPB-TEST-001" in evidence[0].claim


# ------------------------------------------------------------------ coverage


def test_coverage_rules(session):
    _license(session)
    on = date(2026, 9, 17)
    assert coverage(session, "Kenya", PHARMA, on).covered
    assert coverage(session, "kenya ", MEDICAL, on).covered  # case and whitespace insensitive
    assert not coverage(session, "Uganda", PHARMA, on).covered
    assert not coverage(session, "Kenya", ProductCategory.LAPTOP.value, on).covered
    assert not coverage(session, None, PHARMA, on).covered

    expired = coverage(session, "Kenya", PHARMA, date(2027, 7, 1))
    assert not expired.covered and "expired" in expired.reason

    early = coverage(session, "Kenya", PHARMA, date(2025, 12, 31))
    assert not early.covered and "not valid until" in early.reason


def test_category_limited_licence(session):
    _license(session, product_categories=[MEDICAL])
    on = date(2026, 9, 17)
    assert coverage(session, "Kenya", MEDICAL, on).covered
    result = coverage(session, "Kenya", PHARMA, on)
    assert not result.covered and "do not cover" in result.reason


def test_deactivated_licence_no_longer_covers(session):
    lic = _license(session)
    deactivate_license(session, lic.id)
    assert not coverage(session, "Kenya", PHARMA, date(2026, 9, 17)).covered


# ------------------------------------------------------------------ policy


def test_regulated_outreach_inside_coverage_is_allowed(ctx):
    _license(ctx.session)
    result = ctx.policy.evaluate(_regulated_request(ctx), now=ctx.now)
    assert result.decision == Decision.ALLOW, result.reasons


def test_regulated_outreach_outside_coverage_escalates(ctx):
    _license(ctx.session)
    result = ctx.policy.evaluate(_regulated_request(ctx, country="Nigeria"), now=ctx.now)
    assert result.decision == Decision.ESCALATE
    assert "R-REG-04" in result.rule_ids


def test_regulated_outreach_with_no_licence_at_all_escalates(ctx):
    result = ctx.policy.evaluate(_regulated_request(ctx), now=ctx.now)
    assert result.decision == Decision.ESCALATE
    assert "R-REG-04" in result.rule_ids


def test_regulated_outreach_after_expiry_escalates(ctx, clock):
    _license(ctx.session, expires_on="2026-09-01")
    result = ctx.policy.evaluate(_regulated_request(ctx), now=ctx.now)
    assert result.decision == Decision.ESCALATE
    assert any("expired" in reason for reason in result.reasons)


def test_regulated_commitment_outside_coverage_is_blocked(ctx):
    result = ctx.policy.evaluate(
        _regulated_request(ctx, kind=ActionKind.FINANCIAL_COMMITMENT, country="Ghana"), now=ctx.now
    )
    assert result.decision == Decision.BLOCK
    assert "R-REG-05" in result.rule_ids


def test_regulated_commitment_inside_coverage_still_needs_a_human(ctx):
    _license(ctx.session)
    result = ctx.policy.evaluate(
        _regulated_request(ctx, kind=ActionKind.FINANCIAL_COMMITMENT), now=ctx.now
    )
    assert result.decision == Decision.ESCALATE
    assert "R-REG-05" not in result.rule_ids


def test_unregulated_categories_ignore_the_register(ctx):
    request = _regulated_request(ctx, category=ProductCategory.LAPTOP.value, country="Nigeria")
    result = ctx.policy.evaluate(request, now=ctx.now)
    assert "R-REG-04" not in result.rule_ids


# ------------------------------------------------------------------ copy


def test_licence_claims_need_a_covering_licence():
    body = "We are a licensed importer and distributor in Kenya. Reply unsubscribe to opt out."
    blocked = validate_message("Supply", body, {"numbers": []})
    assert not blocked.ok
    assert any(item.startswith("license_claim") for item in blocked.unsupported)
    assert validate_message("Supply", body, {"numbers": [], "license_verified": True}).ok


def _pharma_opportunity(ctx, country):
    company, _ = ctx.memory.upsert_company(name="Pharma Buyer", domain="pharmabuyer.example", country=country)
    contact = ctx.memory.upsert_contact(
        company_id=company.id, full_name="Grace Otieno", role="Procurement Manager",
        email="grace.otieno@pharmabuyer.example", confidence=0.7, source="directory",
    )
    opportunity, _ = ctx.memory.create_opportunity(company.id, PHARMA)
    opportunity.contact_id = contact.id
    opportunity.economics = {"quantity": 40, "lead_time_days": 12}
    opportunity.qualification = {"strategy": {"message_angle": "lead time", "followup_cadence_days": 4}}
    ctx.session.flush()
    return opportunity


def test_licensed_market_outreach_sends_and_states_the_licence(ctx):
    _license(ctx.session)
    opportunity = _pharma_opportunity(ctx, "Kenya")
    result = OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert result.output.get("sent") is True, result.output
    message = ctx.session.scalar(select(Message).where(Message.direction == "outbound"))
    assert "licensed importer and distributor in Kenya" in message.body


def test_unlicensed_market_outreach_goes_to_review(ctx):
    _license(ctx.session)
    opportunity = _pharma_opportunity(ctx, "Nigeria")
    result = OutreachAgent().run(ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert result.output.get("escalated") is True
    assert opportunity.stage == OpportunityStage.COMMERCIAL_REVIEW.value
    assert ctx.session.scalar(select(Message).where(Message.direction == "outbound")) is None


# ------------------------------------------------------------------ expiry alerts


def test_expiry_check_raises_one_alert_per_licence(ctx):
    _license(ctx.session, expires_on="2026-10-10")  # 23 days after the test clock
    first = license_expiry_check(ctx)
    second = license_expiry_check(ctx)
    assert len(first["raised"]) == 1
    assert second["raised"] == []
    events = ctx.session.scalars(select(ComplianceEvent).where(ComplianceEvent.flag == "license_expiring")).all()
    assert len(events) == 1


def test_expired_licence_raises_a_high_severity_alert(ctx):
    _license(ctx.session, expires_on="2026-09-01")
    license_expiry_check(ctx)
    event = ctx.session.scalar(select(ComplianceEvent).where(ComplianceEvent.flag == "license_expired"))
    assert event is not None and event.severity == "high"


def test_distant_expiry_raises_nothing(ctx):
    _license(ctx.session)
    assert license_expiry_check(ctx)["raised"] == []


# ------------------------------------------------------------------ API and CLI


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import reset_settings_cache
    from app.database.session import reset_engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/lic.sqlite3")
    monkeypatch.setenv("NEXUS_MODE", "simulation")
    reset_settings_cache()
    reset_engine()
    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()
    reset_settings_cache()


def test_licence_api_round_trip(client):
    payload = {
        "holder_name": "Leonard Medical Supplies Ltd",
        "country": "Kenya",
        "issuing_authority": "Pharmacy and Poisons Board",
        "license_number": "PPB-API-001",
        "license_types": ["importer", "distributor"],
        "product_categories": [MEDICAL, PHARMA],
        "expires_on": "2030-12-31",
    }
    created = client.post("/api/licenses", json=payload)
    assert created.status_code == 201, created.text
    lic_id = created.json()["id"]
    listed = client.get("/api/licenses").json()
    assert [row["id"] for row in listed] == [lic_id]
    assert client.get("/api/state").json()["licenses"][0]["license_number"] == "PPB-API-001"

    assert client.post("/api/licenses", json=payload).status_code == 422  # duplicate
    assert client.post("/api/licenses", json={**payload, "license_number": "X", "expires_on": "soon"}).status_code == 422

    assert client.post(f"/api/licenses/{lic_id}/deactivate").json()["active"] is False
    assert client.get("/api/licenses").json() == []
    assert len(client.get("/api/licenses", params={"include_inactive": True}).json()) == 1


def test_licence_cli(tmp_path, capsys, monkeypatch):
    from app.cli import main
    from app.core.config import reset_settings_cache
    from app.database.session import reset_engine

    url = f"sqlite+pysqlite:///{tmp_path}/cli.sqlite3"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings_cache()
    reset_engine()
    try:
        code = main([
            "--database-url", url, "license", "add",
            "--holder", "Leonard Medical Supplies Ltd", "--country", "Kenya",
            "--authority", "Pharmacy and Poisons Board", "--number", "PPB-CLI-001",
            "--types", "importer", "distributor", "--categories", MEDICAL, PHARMA,
            "--expires", "2030-12-31",
        ])
        assert code == 0
        added = json.loads(capsys.readouterr().out)
        assert added["country"] == "Kenya"

        assert main(["--database-url", url, "license", "list"]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert listed[0]["license_number"] == "PPB-CLI-001"

        bad = main([
            "--database-url", url, "license", "add",
            "--holder", "X", "--country", "Kenya", "--authority", "Y", "--number", "Z",
            "--types", "importer", "--categories", PHARMA, "--expires", "never",
        ])
        assert bad == 2
    finally:
        reset_engine()
        reset_settings_cache()
