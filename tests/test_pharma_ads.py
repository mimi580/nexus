"""Pharmaceutical ads: off by default; when switched on they stay generic and always wait for approval."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ads import copy as adcopy
from app.ads.agents import AdLaunchAgent, AdPlannerAgent
from app.ads.compliance import ad_allowed, claim_issues
from app.core.interfaces import ActionRequest
from app.core.types import ActionKind, Decision, ProductCategory
from app.database.models import AdCampaign, AdVariant, ReviewItem
from app.site.pages import page_facts
from tests.test_ads import LAPTOP, PHARMA, adctx, run_task  # noqa: F401, F811


@pytest.fixture
def pharma_on(adctx):  # noqa: F811
    adctx.settings = adctx.settings.model_copy(update={"ads_pharma_enabled": True, "ads_require_launch_approval": False})
    adctx.policy.settings = adctx.settings
    return adctx


def test_pharma_ads_are_off_by_default():
    assert not ad_allowed(PHARMA)
    assert ad_allowed(PHARMA, pharma_enabled=True)
    assert ad_allowed(LAPTOP)


def test_planner_skips_pharma_until_enabled(adctx):  # noqa: F811
    result = run_task(adctx, AdPlannerAgent(), {"product_category": PHARMA})
    assert not result.output["campaigns"]
    assert any("R-ADS-01" in n for n in result.output["notes"])


def test_pharma_campaign_is_planned_when_enabled_and_never_names_a_drug(pharma_on):
    result = run_task(pharma_on, AdPlannerAgent(), {"product_category": PHARMA})
    assert result.ok, result.error
    ids = result.output["campaigns"]
    assert ids
    campaign = pharma_on.session.get(AdCampaign, ids[0])
    variants = pharma_on.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id)).all()
    assert variants
    blob = (repr(campaign.__dict__) + repr([v.content for v in variants])).lower()
    assert "amoxicillin" not in blob and "from $" not in blob and "from usd" not in blob
    # restricted terms may appear only as negative keywords, never in ad text or the positive keywords
    text = repr([v.content for v in variants]).lower()
    assert not any(k in text for k in ("prescription", "online pharmacy", "buy online"))
    plan = campaign.plan or {}
    positives = [k.lower() for k in (plan.get("keywords") or [])]
    assert not any(k in kw for kw in positives for k in ("prescription", "online pharmacy", "buy online"))


def test_pharma_page_facts_are_generic(pharma_on):
    facts = page_facts(pharma_on, PHARMA, "Kenya")
    assert facts["from_price_usd"] is None
    assert [p["name"] for p in facts["products"]] == ["Pharmaceutical supply for institutions"]
    assert "amoxicillin" not in repr(facts).lower()


def test_pharma_keywords_have_no_drug_names_even_when_the_model_suggests_them(pharma_on):
    facts = page_facts(pharma_on, PHARMA, "Kenya")
    keywords, negatives, rejected = adcopy.keyword_plan(facts, extra=["amoxicillin 500mg", "buy tramadol online"])
    assert keywords
    assert not any("amoxicillin" in k or "tramadol" in k or "online" in k for k in keywords)
    assert "prescription" in negatives and "dosage" in negatives


@pytest.mark.parametrize("text", [
    "Buy tramadol without a prescription", "Weight loss pills in bulk", "Cures malaria fast",
    "FDA approved medicines", "Viagra wholesale",
])
def test_pharma_copy_with_restricted_claims_is_refused(text):
    assert claim_issues(text, PHARMA)


def test_pharma_launch_always_waits_for_approval_even_when_launch_approval_is_off(pharma_on):
    request = ActionRequest(kind=ActionKind.LAUNCH_AD_CAMPAIGN, summary="x", idempotency_key="p1",
                            payload={"product_category": PHARMA})
    result = pharma_on.policy.evaluate(request)
    assert result.decision == Decision.ESCALATE and "R-ADS-04" in result.rule_ids


def test_pharma_launch_is_blocked_when_disabled(adctx):  # noqa: F811
    request = ActionRequest(kind=ActionKind.LAUNCH_AD_CAMPAIGN, summary="x", idempotency_key="p2",
                            payload={"product_category": PHARMA})
    result = adctx.policy.evaluate(request)
    assert result.decision == Decision.BLOCK and "R-ADS-01" in result.rule_ids


def test_pharma_campaign_reaches_the_review_queue_and_nothing_goes_live(pharma_on):
    ids = run_task(pharma_on, AdPlannerAgent(), {"product_category": PHARMA}).output["campaigns"]
    for cid in ids:
        run_task(pharma_on, AdLaunchAgent(), {"campaign_id": cid})
    pending = list(pharma_on.session.scalars(select(ReviewItem).where(ReviewItem.kind == "ad_campaign",
                                                                      ReviewItem.status == "pending")))
    assert pending
    assert not pharma_on.ads_platforms["google"].calls and not pharma_on.ads_platforms["meta"].calls


def test_other_lines_are_unaffected_by_the_pharma_switch(pharma_on):
    assert pharma_on.settings.ads_require_launch_approval is False
    request = ActionRequest(kind=ActionKind.LAUNCH_AD_CAMPAIGN, summary="x", idempotency_key="p3",
                            payload={"product_category": ProductCategory.LAPTOP.value})
    assert pharma_on.policy.evaluate(request).decision == Decision.ALLOW
