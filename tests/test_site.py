"""Public landing pages and the enquiry form, end to end through the API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import reset_settings_cache
from app.database.session import reset_engine, session_scope


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/site.sqlite3")
    monkeypatch.setenv("NEXUS_MODE", "simulation")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.example-brand.test")
    monkeypatch.setenv("WHATSAPP_NUMBER", "254700000000")
    monkeypatch.setenv("BUSINESS_NAME", "Example Traders")
    reset_settings_cache()
    reset_engine()
    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()
    reset_settings_cache()


@pytest.fixture
def slug(client):
    assert client.post("/api/catalogue/offers", json={
        "product_category": "refurbished_laptop", "product_name": "Dell Latitude 5490 i5 8GB 256GB SSD",
        "condition": "Grade A refurbished", "unit_cost_low_usd": 165, "moq": 20, "lead_time_days": 14,
        "warranty": "6 months", "supplier_name": "Gulf ITAD", "supplier_country": "United Arab Emirates", "source": "Q-1",
    }).status_code == 201
    assert client.post("/api/catalogue/prices", json={
        "product_category": "refurbished_laptop", "unit_price_low_usd": 235, "unit_price_high_usd": 290,
        "basis": "survey", "source": "S-1",
    }).status_code == 201
    response = client.post("/api/pages", json={"product_category": "refurbished_laptop", "country": "Kenya"})
    assert response.status_code == 200, response.text
    return response.json()["slug"]


FORM = {"full_name": "Grace Njeri", "organisation": "Nairobi Academy", "email": "grace@nairobiacademy.ac.ke",
        "phone": "+254700111222", "quantity": "40", "message": "Need laptops for our lab", "consent": "yes"}


def test_page_shows_facts_and_counts_a_view(client, slug):
    page = client.get(f"/p/{slug}?utm_source=google&gclid=abc123")
    assert page.status_code == 200
    assert "Dell Latitude 5490" in page.text and "235" in page.text
    assert "name='gclid' value='abc123'" in page.text  # attribution carried into the form
    assert f"/p/{slug}/wa" in page.text
    pages = client.get("/api/pages").json()
    assert pages[0]["views"] == 1
    client.get(f"/p/{slug}")
    assert client.get("/api/pages").json()[0]["views"] == 1  # one view per visitor per day


def test_enquiry_becomes_a_lead_and_a_deal(client, slug):
    response = client.post(f"/p/{slug}/enquiry", data={**FORM, "gclid": "abc123", "utm_source": "google"})
    assert response.status_code == 200 and "we have your enquiry" in response.text
    leads = client.get("/api/leads").json()
    assert len(leads) == 1 and leads[0]["platform"] == "google" and leads[0]["quantity"] == 40
    client.post("/api/run")
    lead = client.get("/api/leads").json()[0]
    assert lead["opportunity_id"] and lead["status"] == "acknowledged"
    opps = client.get("/api/opportunities").json()
    assert any(o["company"] == "Nairobi Academy" for o in opps)


def test_honeypot_and_validation(client, slug):
    assert client.post(f"/p/{slug}/enquiry", data={**FORM, "website": "http://spam.test"}).status_code == 200
    assert client.get("/api/leads").json() == []  # stored as spam, never shown or processed
    missing = client.post(f"/p/{slug}/enquiry", data={k: v for k, v in FORM.items() if k != "consent"})
    assert missing.status_code == 422 and "agree to be contacted" in missing.text
    bad = client.post(f"/p/{slug}/enquiry", data={**FORM, "email": "not-an-email"})
    assert bad.status_code == 422


def test_whatsapp_click_is_counted_and_redirected(client, slug):
    response = client.get(f"/p/{slug}/wa", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"].startswith("https://wa.me/254700000000")
    assert client.get("/api/pages").json()[0]["whatsapp"] == 1


def test_public_pages_and_unknown_slugs(client, slug):
    assert client.get("/privacy").status_code == 200
    assert slug in client.get("/site").text
    assert client.get("/p/no-such-page").status_code == 404


def test_pharma_page_can_exist_but_ads_cannot_use_photos(client):
    from app.core.config import get_settings

    assert get_settings().nexus_mode == "simulation"
    response = client.post("/api/ads/assets?category=pharmaceutical", content=b"x")
    assert response.status_code == 422


def test_learning_endpoint(client):
    data = client.get("/api/learning?refresh=true").json()
    assert data["scoring"]["active_version"] == 0
    assert "refurbished_laptop" in data["email"]
    assert client.post("/api/learning/rollback", json={}).status_code == 422
