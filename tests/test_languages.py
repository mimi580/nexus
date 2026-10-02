"""Arabic, Turkish and Hebrew: language choice, claim rules, verified translation, pages, ads."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ads import agents as ad_agents
from app.ads.agents import AdLaunchAgent, AdPlannerAgent
from app.ads.compliance import check_keywords, claim_issues
from app.ads.platforms import SimulatedAdsPlatform
from app.agents import outreach as outreach_module
from app.agents.base import BaseAgent
from app.agents.outreach import OutreachAgent
from app.agents.reply import ReplyAgent, draft_reply
from app.commercial import catalogue
from app.commercial import settings as commercial
from app.core import languages
from app.core.config import reset_settings_cache
from app.core.context import build_context
from app.core.interfaces import ActionRequest
from app.core.types import ActionKind, Decision, OpportunityStage, ProductCategory
from app.database.models import AdCampaign, AdVariant, LandingPage, Message, ReviewItem
from app.database.session import reset_engine
from app.policies.engine import PolicyContext, rule_fact_validation
from app.policies.fact_check import validate_message
from app.review import queue

LAPTOP = ProductCategory.LAPTOP.value
MEDICAL = ProductCategory.MEDICAL.value


# ------------------------------------------------------------------ language choice
def test_each_middle_east_country_has_a_language(session):
    from app.policies.licenses import REGIONS

    assert all(languages.language_for(session, c) in ("ar", "tr", "he") for c in REGIONS["MIDDLE_EAST"])
    assert languages.language_for(session, "Saudi Arabia") == "ar"
    assert languages.language_for(session, "UAE") == "ar"
    assert languages.language_for(session, "Turkiye") == "tr"
    assert languages.language_for(session, "Israel") == "he"
    assert languages.language_for(session, "Kenya") == "en"


def test_operator_can_switch_a_country_to_english(session):
    commercial.update(session, {"languages": {"UAE": "en"}})
    assert languages.language_for(session, "United Arab Emirates") == "en"
    with pytest.raises(commercial.CommercialSettingsError):
        commercial.update(session, {"languages": {"Qatar": "fr"}})


def test_every_language_has_every_fixed_string():
    keys = set(languages.STRINGS["en"]) | {"err_many", "err_name", "err_email", "err_consent", "err_qty", "err_qty_range"}
    for code in ("ar", "tr", "he"):
        assert keys <= set(languages.STRINGS[code]), (code, keys - set(languages.STRINGS[code]))
        assert set(languages.CATEGORY_NAMES[code]) == {c.value for c in ProductCategory}


# ------------------------------------------------------------------ claim rules in each language
def test_claims_are_caught_in_arabic_turkish_and_hebrew():
    facts = {"numbers": [], "language": "ar"}
    assert any("relationship_claim" in u for u in validate_message("", "نحن الموزع المعتمد في المنطقة", facts).unsupported)
    assert any("license_claim" in u for u in validate_message("", "شركتنا موزع مرخص", facts).unsupported)
    assert validate_message("", "شركتنا موزع مرخص", {**facts, "license_verified": True}).ok
    assert any("medical_claim" in u for u in validate_message("", "مثبت سريرياً", facts).unsupported)  # vowel marks folded
    assert any("regulatory_claim" in u for u in validate_message("", "FDA onaylı cihazlar", {"numbers": [], "language": "tr"}).unsupported)
    assert any("relationship_claim" in u for u in validate_message("", "אנחנו מפיץ מורשה", {"numbers": [], "language": "he"}).unsupported)
    assert validate_message("", "نورّد معدات طبية للمستشفيات مع ضمان", facts).ok  # warranty and plain supply are fine


def test_figures_are_checked_in_any_script():
    result = validate_message("", "السعر ٩٩ دولار للوحدة", {"numbers": [40], "language": "ar"})
    assert "unsupported_figure:99" in result.unsupported
    assert validate_message("", "الكمية 40 وحدة", {"numbers": [40], "language": "ar"}).ok


def test_ad_claims_and_keywords_in_local_languages():
    assert claim_issues("أفضل سعر في السوق", MEDICAL, "ar")
    assert claim_issues("en ucuz tıbbi cihazlar", MEDICAL, "tr")
    assert claim_issues("12 ay garantili hasta monitörü", MEDICAL, "tr") == []  # "garantili" = with warranty
    assert claim_issues("המחיר הנמוך ביותר", MEDICAL, "he")
    accepted, rejected = check_keywords(["مورد معدات طبية", "tıbbi cihaz tedarikçisi", "ספק ציוד רפואי", "bad<kw>"])
    assert len(accepted) == 3 and rejected == ["bad<kw>"]


def test_opt_out_is_recognised_in_each_language():
    assert languages.opt_out_in("الرجاء إلغاء الاشتراك")
    assert languages.opt_out_in("Lütfen beni listeden çıkar")
    assert languages.opt_out_in("נא הסר אותי")
    assert not languages.opt_out_in("نرجو إرسال عرض سعر لأجهزة المراقبة")
    assert not languages.opt_out_in("Please send a quotation")


# ------------------------------------------------------------------ policy: verified translation
def _request(payload):
    return ActionRequest(kind=ActionKind.SEND_OUTREACH, summary="x", idempotency_key="lang-1",
                         payload={"subject": "عرض", "personalized": True, "allowed_facts": {"numbers": [40]}, **payload})


def test_foreign_message_needs_a_clean_english_back_translation(ctx):
    pctx = PolicyContext(session=ctx.session, settings=ctx.settings, budget=ctx.budget, now=ctx.now)
    body = "نورّد أجهزة لعدد 40 وحدة"
    missing = rule_fact_validation(_request({"body": body, "language": "ar"}), pctx)
    assert missing.decision == Decision.BLOCK and "R-FACT-03" in missing.rule_ids
    lying = rule_fact_validation(_request({"body": body, "language": "ar",
                                           "english_check": "We are the authorized distributor, 40 units"}), pctx)
    assert lying.decision == Decision.BLOCK and "R-FACT-01" in lying.rule_ids
    assert rule_fact_validation(_request({"body": body, "language": "ar", "english_check": "We source devices, 40 units"}), pctx) is None


# ------------------------------------------------------------------ outreach
def _buyer(ctx, country="Saudi Arabia", category=LAPTOP):
    company, _ = ctx.memory.upsert_company(name="Riyadh Academy", domain="riyadhacademy.example", country=country,
                                           buying_signals=["a tender for student laptops"])
    contact = ctx.memory.upsert_contact(company_id=company.id, full_name="Omar Farouk", role="Procurement Manager",
                                        email="omar@riyadhacademy.example", confidence=0.8, source="directory")
    opportunity, _ = ctx.memory.create_opportunity(company.id, category)
    opportunity.contact_id = contact.id
    opportunity.economics = {"quantity": 40, "lead_time_days": 12, "landed_cost_usd": [8000, 8400],
                             "revenue_usd": [12400, 13600], "min_margin_pct": 12, "unknowns": []}
    opportunity.qualification = {"strategy": {"message_angle": "lead time", "followup_cadence_days": 4}}
    ctx.session.flush()
    return opportunity, contact


@pytest.fixture
def named_ctx(session, settings, clock):
    return build_context(session, settings.model_copy(update={"business_name": "Example Traders"}), clock=clock)


def test_outreach_to_an_arabic_market_is_tagged_checked_and_footed_in_arabic(named_ctx):
    opportunity, _ = _buyer(named_ctx)
    result = OutreachAgent().run(named_ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert result.output["sent"] is True, result.output
    message = named_ctx.session.scalar(select(Message).where(Message.direction == "outbound"))
    assert message.fact_check["language"] == "ar"
    assert "إلغاء الاشتراك" in message.body  # opt-out line in the reader's language


def test_outreach_is_blocked_when_the_translation_cannot_be_verified(named_ctx, monkeypatch):
    opportunity, _ = _buyer(named_ctx)
    monkeypatch.setattr(outreach_module, "back_translate", lambda *a, **k: ({}, 0.0))
    result = OutreachAgent().run(named_ctx, {"opportunity_id": opportunity.id, "regulatory_checked": True})
    assert result.output.get("blocked") is True
    assert any("back-translation" in r for r in result.output["reasons"])
    assert named_ctx.session.scalar(select(Message).where(Message.direction == "outbound")) is None


# ------------------------------------------------------------------ replies: read and edit in English
def test_reply_in_arabic_is_reviewed_and_edited_in_english(ctx, monkeypatch):
    opportunity, contact = _buyer(ctx)
    ctx.memory.transition(opportunity, OpportunityStage.OUTREACH, "test")
    inbound = Message(opportunity_id=opportunity.id, contact_id=contact.id, direction="inbound", subject="رد",
                      body="نرجو إرسال الأسعار لعدد 40 جهاز", status="received", dedupe_key="in-ar",
                      provider_message_id="<r@riyadhacademy.example>")
    ctx.session.add(inbound)
    ctx.session.flush()

    def fake_ask(self, ctx_, prompt, context, *, task_type=None, **_kw):
        if task_type == "translation" and context["direction"] == "to_english":
            return {"fields": {"subject": "Reply", "body": "Our indicative price is USD 310 per unit for 40 units."}}, 0.0
        if task_type == "translation":
            return {"fields": {"body": "نص معدّل: السعر الاسترشادي 310 دولار للوحدة"}}, 0.0
        return {"subject": "رد: عرض سعر", "body": "السعر الاسترشادي 310 دولار أمريكي للوحدة لعدد 40 وحدة.",
                "language": "ar", "their_message_english": "Please send prices for 40 devices"}, 0.0

    monkeypatch.setattr(BaseAgent, "ask", fake_ask)
    agent = ReplyAgent()
    task, _ = ctx.tasks.create_task(agent="inbound_quote", objective_id=None, task_input={"opportunity_id": opportunity.id})
    ctx.task_id = task.id
    assert draft_reply(agent, ctx, inbound, opportunity, contact, "price_request") is True
    ctx.task_id = None
    item = ctx.session.scalar(select(ReviewItem).where(ReviewItem.kind == "reply_approval"))
    payload = item.action_payload
    assert payload["language"] == "ar" and payload["body_english"].startswith("Our indicative price")
    assert payload["their_message_english"] == "Please send prices for 40 devices"

    queue.decide(ctx, item.id, "approve", body_english="Our indicative price is USD 310 per unit. Delivery to Riyadh.")
    assert item.action_payload["retranslate"] is True
    result = agent.run(ctx, {"review_id": item.id})
    assert result.ok and result.output["sent"] is True, result
    sent = ctx.session.scalar(select(Message).where(Message.direction == "outbound"))
    assert sent.body.startswith("نص معدّل")  # the edited English, translated, is what went out


# ------------------------------------------------------------------ ads
@pytest.fixture
def adctx(session, settings, clock):
    s = settings.model_copy(update={"public_site_url": "https://www.example-brand.test", "business_name": "Example Traders",
                                    "ads_require_launch_approval": False})
    ctx = build_context(session, s, clock=clock)
    ctx.ads_platforms = {"google": SimulatedAdsPlatform("google"), "meta": SimulatedAdsPlatform("meta")}
    catalogue.add_offer(session, product_category=MEDICAL, product_name="Patient monitor, 5-parameter", condition="New",
                        unit_cost_low_usd=780, moq=2, lead_time_days=30, warranty="12 months",
                        supplier_name="MedSource", supplier_country="Germany", source="quote M-1")
    catalogue.add_price_reference(session, product_category=MEDICAL, unit_price_low_usd=1100, unit_price_high_usd=1400,
                                  basis="survey", source="S-2")
    commercial.update(session, {"target_markets": {MEDICAL: ["Saudi Arabia"], LAPTOP: [], "used_iphone": [], "server_it": []}})
    return ctx


def _plan(ctx, platform):
    agent = AdPlannerAgent()
    task, _ = ctx.tasks.create_task(agent=agent.name, objective_id=None, task_input={"platform": platform})
    ctx.task_id = task.id
    try:
        return agent.run(ctx, {"platform": platform})
    finally:
        ctx.task_id = None


def test_campaign_for_saudi_arabia_runs_in_arabic(adctx):
    result = _plan(adctx, "google")
    assert result.ok and result.output["campaigns"], result.output
    campaign = adctx.session.scalar(select(AdCampaign))
    assert campaign.language == "ar"
    assert "مورد معدات طبية" in campaign.targeting["keywords"]
    assert "معدات طبية السعودية" in campaign.targeting["keywords"]
    assert "وظائف" in campaign.targeting["negative_keywords"] and "jobs" in campaign.targeting["negative_keywords"]
    page = adctx.session.get(LandingPage, campaign.landing_page_id)
    assert page.language == "ar" and page.slug.endswith("-ar")
    english = adctx.session.scalar(select(LandingPage).where(LandingPage.language == "en"))
    assert english is not None and english.country == "Saudi Arabia"
    variants = list(adctx.session.scalars(select(AdVariant)))
    assert variants and all("english" in v.content for v in variants)  # you can always read what the ad says
    AdLaunchAgent().run(adctx, {"campaign_id": campaign.id})
    assert campaign.status == "active"


def test_campaign_falls_back_to_english_when_translation_fails(adctx, monkeypatch):
    monkeypatch.setattr(ad_agents, "localise_variant", lambda *a, **k: (None, ["limit"], 0.0))
    result = _plan(adctx, "meta")
    assert result.ok and result.output["campaigns"], result.output
    campaign = adctx.session.scalar(select(AdCampaign))
    assert campaign.language == "en"
    assert not adctx.session.get(LandingPage, campaign.landing_page_id).slug.endswith("-ar")


def test_localised_ad_is_rejected_when_its_meaning_breaks_the_rules(adctx, monkeypatch):
    from app.ads import copy as adcopy
    from app.site.pages import page_facts

    facts = page_facts(adctx, MEDICAL, "Saudi Arabia", "ar")
    content = adcopy.template("meta", facts, "price_value")
    monkeypatch.setattr(ad_agents, "back_translate", lambda *a, **k: ({"headline": "Guaranteed best price"}, 0.0))
    local, issues, _ = ad_agents.localise_variant(AdPlannerAgent(), adctx, "meta", facts, content, "ar")
    assert local is None and any("banned_claim" in i for i in issues)


# ------------------------------------------------------------------ pages and enquiries (through the API)
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/lang.sqlite3")
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


def test_arabic_page_is_right_to_left_with_an_english_twin(client):
    assert client.post("/api/catalogue/offers", json={
        "product_category": MEDICAL, "product_name": "Patient monitor, 5-parameter", "condition": "New",
        "unit_cost_low_usd": 780, "moq": 2, "lead_time_days": 30, "warranty": "12 months",
        "supplier_name": "MedSource", "supplier_country": "Germany", "source": "M-1"}).status_code == 201
    assert client.post("/api/catalogue/prices", json={
        "product_category": MEDICAL, "unit_price_low_usd": 1100, "unit_price_high_usd": 1400,
        "basis": "survey", "source": "S-2"}).status_code == 201
    made = client.post("/api/pages", json={"product_category": MEDICAL, "country": "Saudi Arabia"})
    assert made.status_code == 200, made.text
    slug = made.json()["slug"]
    assert slug.endswith("-ar") and made.json()["english_slug"]
    page = client.get(f"/p/{slug}")
    assert "<html lang='ar' dir='rtl'>" in page.text
    assert "اطلب عرض سعر" in page.text and "السعودية" in page.text and ">English</a>" in page.text
    english = client.get(f"/p/{made.json()['english_slug']}")
    assert "<html lang='en'>" in english.text and "العربية" in english.text

    bad = client.post(f"/p/{slug}/enquiry", data={"full_name": "Omar", "organisation": "Riyadh Clinic",
                                                 "email": "nope", "consent": "yes"})
    assert bad.status_code == 422 and "يرجى إدخال بريد إلكتروني صحيح" in bad.text
    ok = client.post(f"/p/{slug}/enquiry", data={"full_name": "Omar Farouk", "organisation": "Riyadh Clinic",
                                                "email": "omar@riyadhclinic.example", "quantity": "12", "consent": "yes",
                                                "message": "نحتاج أجهزة مراقبة"})
    assert ok.status_code == 200 and "استلمنا طلبك" in ok.text
    client.post("/api/run")
    assert client.get("/api/leads").json()[0]["status"] == "acknowledged"
