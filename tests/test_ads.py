"""Advertising: compliance, copy, planning, launch approval, spend tracking, optimisation, conversions."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.ads import copy as adcopy
from app.ads.agents import (
    AdLaunchAgent,
    AdOptimizerAgent,
    AdPlannerAgent,
    ConversionUploadAgent,
    ads_sync,
    reallocate_budgets,
)
from app.ads.compliance import ad_allowed, check_google, check_meta, claim_issues
from app.ads.platforms import SimulatedAdsPlatform
from app.commercial import catalogue
from app.core.context import build_context
from app.core.interfaces import ActionRequest
from app.core.types import ActionKind, Decision, ProductCategory
from app.database.models import AdCampaign, AdMetric, AdVariant, LandingPage, Lead, ReviewItem, Task
from app.review import queue
from app.site.pages import LandingPageAgent, page_facts

LAPTOP = ProductCategory.LAPTOP.value
PHARMA = ProductCategory.PHARMA.value


def seed_catalogue(session):
    catalogue.add_offer(session, product_category=LAPTOP, product_name="Dell Latitude 5490 i5 8GB 256GB SSD",
                        condition="Grade A refurbished", unit_cost_low_usd=165, unit_cost_high_usd=180, moq=20,
                        lead_time_days=14, warranty="6 months", supplier_name="Gulf ITAD", supplier_country="United Arab Emirates",
                        source="quote Q-1")
    catalogue.add_offer(session, product_category=LAPTOP, product_name="Lenovo ThinkPad T480 i5 8GB 256GB SSD",
                        condition="Grade A refurbished", unit_cost_low_usd=175, moq=20, lead_time_days=14,
                        warranty="6 months", supplier_name="Gulf ITAD", supplier_country="United Arab Emirates", source="quote Q-1")
    catalogue.add_offer(session, product_category=PHARMA, product_name="Amoxicillin 500mg", unit_cost_low_usd=2,
                        supplier_name="Pharma Exports", supplier_country="India", source="quote Q-2")
    catalogue.add_price_reference(session, product_category=LAPTOP, unit_price_low_usd=235, unit_price_high_usd=290,
                                  basis="market survey", source="survey 2026-09")


@pytest.fixture
def adctx(session, settings, clock):
    s = settings.model_copy(update={"public_site_url": "https://www.example-brand.test", "business_name": "Example Traders"})
    ctx = build_context(session, s, clock=clock)
    ctx.ads_platforms = {"google": SimulatedAdsPlatform("google"), "meta": SimulatedAdsPlatform("meta")}
    seed_catalogue(session)
    commercial_markets = {"target_markets": {LAPTOP: ["Kenya", "Uganda"], PHARMA: ["Kenya"]}}
    from app.commercial import settings as commercial

    commercial.update(session, commercial_markets)
    return ctx


def run_task(ctx, agent, task_input):
    task, _ = ctx.tasks.create_task(agent=agent.name, objective_id=None, task_input=task_input)
    ctx.task_id = task.id
    try:
        return agent.run(ctx, task_input)
    finally:
        ctx.task_id = None


def plan_and_launch(ctx, **filters):
    result = run_task(ctx, AdPlannerAgent(), filters)
    assert result.ok, result.error
    ids = result.output["campaigns"]
    for cid in ids:
        run_task(ctx, AdLaunchAgent(), {"campaign_id": cid})
    return ids


def approve_all(ctx):
    for item in list(ctx.session.scalars(select(ReviewItem).where(ReviewItem.kind == "ad_campaign", ReviewItem.status == "pending"))):
        queue.decide(ctx, item.id, "approve")
        task = next(t for t in ctx.session.scalars(select(Task).where(Task.agent == "ad_launch"))
                    if (t.input or {}).get("approved_review_id") == item.id)
        run_task(ctx, AdLaunchAgent(), dict(task.input))


# ------------------------------------------------------------------ compliance and copy


def test_pharmaceuticals_are_never_advertised():
    assert not ad_allowed(PHARMA)
    assert ad_allowed(LAPTOP)


def test_used_goods_cannot_be_called_new_and_no_guarantees():
    issues = claim_issues("Brand new iPhones, guaranteed lowest price", ProductCategory.IPHONE.value)
    assert any("brand new" in i for i in issues) and any("guarantee" in i for i in issues)


def test_platform_limits_are_enforced():
    facts = {"category": LAPTOP, "licence_statement": None}
    long = {"headlines": ["x" * 31, "b", "c"], "descriptions": ["d", "e"]}
    assert any("over 30" in i for i in check_google(long, LAPTOP, facts, []))
    meta = {"primary_text": "ok", "headline": "h" * 41, "call_to_action": "BUY_NOW"}
    issues = check_meta(meta, LAPTOP, facts, [])
    assert any("headline over 40" in i for i in issues) and any("call to action" in i for i in issues)


def test_unsupported_figures_fail_the_check(adctx):
    facts = page_facts(adctx, LAPTOP, "Kenya")
    content = adcopy.google_template(facts, "price_value")
    content["headlines"][0] = "From USD 99 per Unit"
    assert any("unsupported_figure:99" in i for i in adcopy.check_variant("google", content, facts))


@pytest.mark.parametrize("platform", ["google", "meta"])
def test_every_template_angle_passes_its_own_checks(adctx, platform):
    facts = page_facts(adctx, LAPTOP, "Kenya")
    angles = adcopy.available_angles(facts)
    assert {"price_value", "lead_time", "quality_warranty", "range"} <= set(angles)
    assert "compliance_docs" not in angles  # no licence claim for laptops
    for angle in angles:
        content = adcopy.template(platform, facts, angle)
        assert adcopy.check_variant(platform, content, facts) == [], (angle, content)


def test_keyword_plan_keeps_intent_and_drops_junk(adctx):
    facts = page_facts(adctx, LAPTOP, "Kenya")
    keywords, negatives, rejected = adcopy.keyword_plan(facts, ["free laptops", "laptop repair nairobi", "Bulk laptops for schools"])
    assert "refurbished laptops kenya" in keywords and "bulk laptops for schools" in keywords
    assert "free laptops" in rejected and "laptop repair nairobi" in rejected
    assert "jobs" in negatives


# ------------------------------------------------------------------ planning and launch


def test_planner_builds_campaigns_with_pages_and_never_pharma(adctx):
    result = run_task(adctx, AdPlannerAgent(), {})
    assert result.ok
    campaigns = list(adctx.session.scalars(select(AdCampaign)))
    assert campaigns and all(c.product_category == LAPTOP for c in campaigns)
    assert {c.platform for c in campaigns} == {"google", "meta"}
    assert any("never advertised" in n for n in result.output["notes"])
    page = adctx.session.scalar(select(LandingPage).where(LandingPage.product_category == LAPTOP))
    assert page is not None and page.status == "published"
    google = next(c for c in campaigns if c.platform == "google")
    assert google.targeting["keywords"] and "jobs" in google.targeting["negative_keywords"]
    meta_variants = list(adctx.session.scalars(select(AdVariant).join(AdCampaign).where(AdCampaign.platform == "meta")))
    assert meta_variants and all(v.asset_id for v in meta_variants)  # a generated card when no photos exist
    assert sum(c.daily_budget_usd for c in campaigns) <= 500
    assert {t["agent"] for t in result.next_tasks} == {"ad_launch"}


def test_platforms_take_turns_when_budget_is_tight(adctx):
    adctx.settings = adctx.settings.model_copy(update={"ads_default_daily_budget_usd": 8.0})
    run_task(adctx, AdPlannerAgent(), {})
    platforms = [c.platform for c in adctx.session.scalars(select(AdCampaign).order_by(AdCampaign.created_at))]
    assert platforms[:2] == ["google", "meta"] and "meta" in platforms


def test_launch_waits_for_approval_then_goes_live(adctx):
    ids = plan_and_launch(adctx, platform="google", country="Kenya")
    campaign = adctx.session.get(AdCampaign, ids[0])
    assert campaign.status == "awaiting_approval"
    item = adctx.session.scalar(select(ReviewItem).where(ReviewItem.kind == "ad_campaign"))
    assert item is not None and "USD/day" in item.action_payload["ad_preview"]
    approve_all(adctx)
    assert campaign.status == "active" and campaign.external_ids["campaign_id"]
    assert adctx.ads_platforms["google"].calls[0] == ("create", campaign.id)
    assert all(v.external_id for v in adctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id)))


def test_rejected_campaign_is_never_launched(adctx):
    ids = plan_and_launch(adctx, platform="meta", country="Kenya")
    item = adctx.session.scalar(select(ReviewItem).where(ReviewItem.kind == "ad_campaign"))
    queue.decide(adctx, item.id, "reject", note="not now")
    assert adctx.session.get(AdCampaign, ids[0]).status == "rejected"
    assert not adctx.ads_platforms["meta"].calls


def test_policy_blocks_pharma_and_failing_copy(adctx):
    request = ActionRequest(kind=ActionKind.LAUNCH_AD_CAMPAIGN, summary="x", payload={"product_category": PHARMA},
                            idempotency_key="t1")
    result = adctx.policy.evaluate(request)
    assert result.decision == Decision.BLOCK and "R-ADS-01" in result.rule_ids
    request = ActionRequest(kind=ActionKind.LAUNCH_AD_CAMPAIGN, summary="x", idempotency_key="t2",
                            payload={"product_category": LAPTOP, "compliance_issues": ["banned_claim:guaranteed"]})
    assert "R-ADS-02" in adctx.policy.evaluate(request).rule_ids


def test_production_requires_ads_enabled(adctx):
    adctx.policy.settings = adctx.settings.model_copy(update={"nexus_mode": "production", "ads_enabled": False})
    request = ActionRequest(kind=ActionKind.LAUNCH_AD_CAMPAIGN, summary="x", payload={"product_category": LAPTOP},
                            idempotency_key="t3")
    assert "R-ADS-00" in adctx.policy.evaluate(request).rule_ids


# ------------------------------------------------------------------ spend, leads, budget


def launched(adctx, platform="google", budget=5.0):
    adctx.settings = adctx.settings.model_copy(update={"ads_require_launch_approval": False,
                                                       "ads_default_daily_budget_usd": budget})
    adctx.policy.settings = adctx.settings
    ids = plan_and_launch(adctx, platform=platform, country="Kenya")
    campaign = adctx.session.get(AdCampaign, ids[0])
    assert campaign.status == "active"
    return campaign


def test_sync_books_spend_once_and_turns_clicks_into_leads(adctx, clock):
    campaign = launched(adctx, budget=15.0)
    clock.advance(days=2)
    first = ads_sync(adctx)
    spent = adctx.budget.committed(category="ads")
    assert first["spend_booked_usd"] > 0 and spent == pytest.approx(first["spend_booked_usd"], abs=0.01)
    assert adctx.session.scalar(select(func.count()).select_from(AdMetric)) > 0
    second = ads_sync(adctx)
    assert second["spend_booked_usd"] == 0  # same days again: nothing double-counted
    leads = list(adctx.session.scalars(select(Lead).where(Lead.campaign_id == campaign.id)))
    assert leads and all(lead.platform == "google" and lead.variant_key for lead in leads)
    assert adctx.session.scalar(select(func.count()).select_from(Task).where(Task.agent == "inbound_lead")) == len(leads)


def test_everything_pauses_at_the_ads_budget(adctx, clock):
    campaign = launched(adctx, "meta")
    adctx.budget.record_incurred("ads", 299.0, reason="earlier spend")
    clock.advance(days=1)
    result = ads_sync(adctx)
    assert result["paused_for_budget"] >= 1 and campaign.status == "paused_budget"
    assert ("status", campaign.id, False) in adctx.ads_platforms["meta"].calls


# ------------------------------------------------------------------ optimisation


def add_metric(ctx, campaign, key, clicks, spend, day):
    ctx.session.add(AdMetric(campaign_id=campaign.id, variant_key=key, day=day, impressions=clicks * 30, clicks=clicks,
                             spend_usd=spend, spend_native=spend, ledgered_usd=spend))


def add_leads(ctx, campaign, key, n):
    for i in range(n):
        ctx.session.add(Lead(campaign_id=campaign.id, variant_key=key, platform=campaign.platform, full_name="A B",
                             email=f"a{key}{i}@x.test", product_category=campaign.product_category, status="new"))


def test_campaign_spending_without_enquiries_is_paused(adctx, clock):
    campaign = launched(adctx)
    key = adctx.session.scalar(select(AdVariant.key).where(AdVariant.campaign_id == campaign.id))
    add_metric(adctx, campaign, key, 300, 60.0, clock.now.date())
    adctx.session.flush()
    result = run_task(adctx, AdOptimizerAgent(), {"campaign_id": campaign.id})
    assert campaign.status == "paused_no_leads" and "no enquiries" in result.output["paused"]


def test_losing_ad_is_replaced_by_a_new_angle(adctx, clock):
    campaign = launched(adctx)
    variants = list(adctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id)))
    assert len(variants) == 3
    day = clock.now.date()
    for i, v in enumerate(variants):
        add_metric(adctx, campaign, v.key, 400, 40.0, day)
        add_leads(adctx, campaign, v.key, 0 if i == 0 else 30)
    adctx.session.flush()
    result = run_task(adctx, AdOptimizerAgent(), {"campaign_id": campaign.id})
    assert variants[0].status == "paused"
    assert any(a.startswith("added ad") for a in result.output["actions"])
    live = [v for v in adctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id)) if v.status == "active"]
    assert len(live) == 3 and variants[0].angle not in {v.angle for v in live}


def test_wasted_search_terms_become_negatives(adctx):
    campaign = launched(adctx)
    run_task(adctx, AdOptimizerAgent(), {"campaign_id": campaign.id})
    assert "free laptop download" in campaign.targeting["negative_keywords"]
    assert "refurbished laptops wholesale" not in campaign.targeting["negative_keywords"]


def test_budget_moves_toward_campaigns_that_bring_enquiries(adctx, clock):
    adctx.settings = adctx.settings.model_copy(update={"ads_require_launch_approval": False})
    adctx.policy.settings = adctx.settings
    ids = plan_and_launch(adctx, platform="google")
    assert len(ids) == 2
    good, bad = (adctx.session.get(AdCampaign, i) for i in ids)
    before = (good.daily_budget_usd, bad.daily_budget_usd)
    day = clock.now.date()
    add_metric(adctx, good, "x", 200, 50.0, day)
    add_leads(adctx, good, "x", 12)
    add_metric(adctx, bad, "y", 200, 50.0, day)
    adctx.session.flush()
    reallocate_budgets(adctx)
    assert good.daily_budget_usd > before[0] and bad.daily_budget_usd < before[1]
    assert good.daily_budget_usd <= round(before[0] * 1.3, 2) + 0.01  # bounded step
    assert bad.daily_budget_usd >= adctx.settings.ads_min_daily_budget_usd


def test_conversions_are_reported_once(adctx):
    campaign = launched(adctx)
    lead = Lead(campaign_id=campaign.id, platform="google", full_name="A B", email="a@b.test", product_category=LAPTOP,
                attribution={"gclid": "abc"}, status="acknowledged")
    adctx.session.add(lead)
    adctx.session.flush()
    agent = ConversionUploadAgent()
    assert run_task(adctx, agent, {"lead_id": lead.id, "event": "lead"}).output["sent"] is True
    assert run_task(adctx, agent, {"lead_id": lead.id, "event": "lead"}).output == {"already_sent": True}
    assert len([c for c in adctx.ads_platforms["google"].calls if c[0] == "conversion"]) == 1
    assert lead.conversions_sent.get("lead")


def test_landing_page_falls_back_to_template_when_copy_breaks_rules(adctx, monkeypatch):
    agent = LandingPageAgent()
    monkeypatch.setattr(agent, "ask", lambda *a, **k: ({"headline": "Guaranteed lowest price laptops", "benefits": []}, 0.0))
    result = agent.run(adctx, {"product_category": LAPTOP, "country": "Kenya"})
    assert result.ok and result.output["template"] is True
    assert any("guarantee" in i for i in result.output["rejected_copy_issues"])
