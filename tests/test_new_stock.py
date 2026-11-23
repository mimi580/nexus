"""New stock alongside used/refurbished: nothing may misdescribe the condition of what is sold."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ads import copy as adcopy
from app.ads.compliance import check_google, claim_issues
from app.core.condition import classify, stock_profile
from app.core.config import reset_settings_cache
from app.database.session import reset_engine
from app.site.pages import allowed_numbers, category_title, slug_for


@pytest.mark.parametrize("text,kind", [
    ("New", "new"), ("brand new", "new"), ("New surplus", "new"), ("factory sealed", "new"), ("NOS", "new"),
    ("Grade A refurbished", "used"), ("Refurbished, tested", "used"), ("used", "used"), ("like new", "used"),
    ("Open box", "used"), ("ex-lease", "used"), ("Pre-owned", "used"), ("renewed", "used"),
    ("", "unknown"), (None, "unknown"), ("excellent", "unknown"), ("tested and working", "unknown"),
])
def test_condition_is_classified_conservatively(text, kind):
    assert classify(text) == kind


def test_stock_profile_describes_the_whole_line():
    assert stock_profile([]) == "unknown"
    assert stock_profile([None, ""]) == "unknown"
    assert stock_profile(["New", "brand new"]) == "new"
    assert stock_profile(["Grade A refurbished", "used"]) == "used"
    assert stock_profile(["New", "Grade B"]) == "mixed"
    assert stock_profile(["New", None]) == "mixed"  # an unstated offer is never assumed new


def test_new_claims_need_new_stock():
    for facts in (None, {"stock": "used"}, {"stock": "mixed"}, {"stock": "unknown"}):
        assert "banned_claim:brand new" in claim_issues("Brand new laptops", "refurbished_laptop", "en", facts)
    assert claim_issues("Brand new laptops in stock", "refurbished_laptop", "en", {"stock": "new"}) == []


def test_sealed_and_manufacturer_warranty_need_their_own_facts():
    new = {"stock": "new"}
    assert "banned_claim:factory sealed" in claim_issues("Factory sealed iPhones", "used_iphone", "en", new)
    assert claim_issues("Factory sealed iPhones", "used_iphone", "en", {**new, "sealed": True}) == []
    assert "banned_claim:manufacturer warranty" in claim_issues("Manufacturer warranty", "refurbished_laptop", "en", new)
    assert claim_issues("Manufacturer warranty", "refurbished_laptop", "en", {**new, "manufacturer_warranty": True}) == []
    # endorsement claims are never relaxed
    assert "banned_claim:apple authorized" in claim_issues("Apple authorized reseller", "used_iphone", "en", {**new, "sealed": True})


def test_new_stock_is_never_called_refurbished():
    issues = claim_issues("Refurbished Dell laptops", "refurbished_laptop", "en", {"stock": "new"})
    assert "misdescribes_condition:refurbished" in issues
    assert claim_issues("Refurbished Dell laptops", "refurbished_laptop", "en", {"stock": "used"}) == []


def test_page_titles_and_ad_nouns_follow_the_stock_but_urls_do_not_move():
    assert category_title("refurbished_laptop", "new") == "Business laptops"
    assert category_title("refurbished_laptop", "used") == "Refurbished business laptops"
    assert category_title("refurbished_laptop", "mixed") == "New and refurbished business laptops"
    assert category_title("used_iphone", "new") == "iPhones"
    assert adcopy.noun_for("used_iphone", "new") == "iPhones"
    assert adcopy.noun_for("server_it", "mixed") == "New & Refurbished Servers"
    assert adcopy.noun_for("medical_equipment", "new") == "Medical Equipment"
    assert slug_for("refurbished_laptop", "Kenya") == "refurbished-business-laptops-kenya"


def test_keywords_match_the_stock():
    for category in ("refurbished_laptop", "used_iphone", "server_it", "medical_equipment"):
        new_bank = adcopy.keywords_for(category, "new")
        assert new_bank and not any("refurbished" in k or "used" in k.split() for k in new_bank), category
    used_bank = adcopy.keywords_for("refurbished_laptop", "used")
    assert any("refurbished" in k for k in used_bank)
    mixed = adcopy.keywords_for("refurbished_laptop", "mixed")
    assert set(used_bank) < set(mixed) and any("new " in k for k in mixed)


def _facts(stock, condition):
    return {
        "category": "refurbished_laptop", "country": "Zambia", "stock": stock, "sealed": False,
        "manufacturer_warranty": False, "category_title": category_title("refurbished_laptop", stock),
        "buyers": "schools and businesses", "from_price_usd": 410, "whatsapp": True,
        "response_promise": "within one business day", "licence_statement": None,
        "products": [{"name": "Dell Latitude 5440", "condition": condition, "warranty": "12 months",
                      "lead_time_days": 14, "moq": 10}],
    }


@pytest.mark.parametrize("angle", ["price_value", "lead_time", "quality_warranty", "local_support", "range"])
def test_templates_for_new_stock_pass_every_ad_check_and_never_say_refurbished(angle):
    facts = _facts("new", "New")
    ad = adcopy.google_template(facts, angle)
    text = " ".join(ad["headlines"] + ad["descriptions"]).lower()
    assert "refurbished" not in text and "graded" not in text
    assert check_google(ad, "refurbished_laptop", facts, allowed_numbers(facts)) == []


def test_templates_for_used_stock_still_say_refurbished_and_pass():
    facts = _facts("used", "Grade A refurbished")
    ad = adcopy.google_template(facts, "price_value")
    assert any("Refurbished" in h for h in ad["headlines"])
    assert check_google(ad, "refurbished_laptop", facts, allowed_numbers(facts)) == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/ns.sqlite3")
    monkeypatch.setenv("NEXUS_MODE", "simulation")
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://www.example-brand.test")
    monkeypatch.setenv("BUSINESS_NAME", "Example Traders")
    reset_settings_cache()
    reset_engine()
    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()
    reset_settings_cache()


def _publish(client, condition, warranty="12 months"):
    assert client.post("/api/catalogue/offers", json={
        "product_category": "refurbished_laptop", "product_name": "Dell Latitude 5440 i5 16GB 512GB",
        "condition": condition, "unit_cost_low_usd": 410, "moq": 10, "lead_time_days": 14, "warranty": warranty,
        "supplier_name": "Gulf IT", "supplier_country": "United Arab Emirates", "source": "Q-9",
    }).status_code == 201
    assert client.post("/api/catalogue/prices", json={
        "product_category": "refurbished_laptop", "unit_price_low_usd": 520, "unit_price_high_usd": 600,
        "basis": "survey", "source": "S-9",
    }).status_code == 201
    response = client.post("/api/pages", json={"product_category": "refurbished_laptop", "country": "Zambia"})
    assert response.status_code == 200, response.text
    return response.json()["slug"]


def _visible(client, slug):
    """The page as a visitor reads it (the address itself keeps its old, stable wording)."""
    return client.get(f"/p/{slug}").text.lower().replace(slug, "")


def test_landing_page_for_new_laptops_never_says_refurbished(client):
    slug = _publish(client, "New", warranty="12 months manufacturer warranty")
    assert slug == "refurbished-business-laptops-zambia"  # the address does not move
    assert "refurbished" not in _visible(client, slug)
    assert client.get("/api/pages").json()[0]["headline"] == "Business laptops for Zambia"


def test_landing_page_for_refurbished_laptops_still_says_so(client):
    slug = _publish(client, "Grade A refurbished", warranty="6 months")
    assert "refurbished" in _visible(client, slug)
    assert client.get("/api/pages").json()[0]["headline"] == "Refurbished business laptops for Zambia"
