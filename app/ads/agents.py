"""The advertising agents: plan, launch, measure, optimise, and report conversions back.

    plan      pick markets and angles (learned), write and check the copy, size the budget
    launch    policy gate (R-ADS), then build the campaign on Google or Meta
    sync      pull spend and clicks daily, book spend against the ads budget, stop at the limit
    optimise  prune losing ads, write replacements, shift budget to what brings enquiries,
              block wasted search terms, pause campaigns that spend without enquiries
    convert   tell the platform which clicks became enquiries and sales, so its own
              bidding learns who your buyers are

Pharmaceuticals are never advertised (R-ADS-01). Budget moves are bounded
(±30% a day, never below the minimum) so one noisy day cannot swing spend.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.ads import copy as adcopy
from app.ads.compliance import ad_allowed, english_meaning_issues
from app.ads.platforms import AdPlatformError, Launch, SimulatedAdsPlatform, platform_for
from app.agents.base import BaseAgent
from app.agents.translate import back_translate, flatten, translate_fields
from app.core import languages
from app.commercial import catalogue
from app.commercial import settings as commercial
from app.core.context import RunContext
from app.core.ids import stable_key
from app.core.interfaces import ActionRequest, AgentResult
from app.core.types import ActionKind, Decision, ModelTier
from app.database.models import AdAsset, AdCampaign, AdMetric, AdVariant, LandingPage, Lead
from app.learning import signals
from app.learning.bandits import CountArm, RateArm, probability_best, thompson_allocation
from app.site.pages import LandingPageAgent, allowed_numbers, ensure_page, page_url

PLATFORMS = ("google", "meta")
LIVE_STATUSES = ("proposed", "awaiting_approval", "active", "paused_budget")
VARIANTS_PER_PLATFORM = {"google": 3, "meta": 4}
MAX_DAILY_CHANGE = 0.30
PRUNE_PROB_BEST = 0.05


# ------------------------------------------------------------------ helpers
def fx_to_native(ctx: RunContext, usd: float, currency: str) -> float:
    rate = ctx.settings.fx_rates.get(currency.upper())
    if rate is None:
        raise AdPlatformError(f"no exchange rate for {currency} in ADS_FX_RATES_JSON")
    return round(usd * rate, 2)


def fx_to_usd(ctx: RunContext, native: float, currency: str) -> float:
    rate = ctx.settings.fx_rates.get(currency.upper())
    if rate is None:
        raise AdPlatformError(f"no exchange rate for {currency} in ADS_FX_RATES_JSON")
    return round(native / rate, 4)


def days_left_in_month(now: datetime) -> int:
    return calendar.monthrange(now.year, now.month)[1] - now.day + 1


def daily_pool_usd(ctx: RunContext) -> float:
    """What ads may spend per day from here to month end: 80% of what is left, spread evenly."""
    if ctx.budget.hard_stopped():
        return 0.0
    remaining = min(ctx.budget.category_remaining("ads"), ctx.budget.remaining())
    return max(0.0, remaining * 0.8 / days_left_in_month(ctx.now))


def ads_active(ctx: RunContext) -> bool:
    return ctx.settings.ads_enabled or ctx.settings.nexus_mode != "production"


def url_suffix(platform: str, campaign: AdCampaign) -> str:
    ad_macro = "{creative}" if platform == "google" else "{{ad.id}}"
    return (f"utm_source={platform}&utm_medium={'cpc' if platform == 'google' else 'paid_social'}"
            f"&utm_campaign={campaign.id}&nx={campaign.id}&nv={ad_macro}")


def _variant_image(ctx: RunContext, campaign: AdCampaign, variant: AdVariant, index: int) -> AdAsset:
    """The operator's photos in rotation; a generated card when there are none."""
    photos = list(ctx.session.scalars(select(AdAsset).where(
        AdAsset.product_category == campaign.product_category, AdAsset.kind == "photo", AdAsset.active.is_(True))
        .order_by(AdAsset.created_at)))
    if photos:
        return photos[index % len(photos)]
    from app.ads.creative import generated_card

    card = variant.content.get("card") or {}
    data = generated_card(campaign.product_category, card.get("headline", ""), card.get("subline", ""),
                          card.get("cta", "Get a quotation"), ctx.settings.business_name or "NEXUS Sourcing")
    asset = AdAsset(product_category=campaign.product_category, kind="generated", filename=f"{variant.key}.png",
                    content_type="image/png", data=data, caption=card.get("headline", ""))
    ctx.session.add(asset)
    ctx.session.flush()
    return asset


def rank_angles(ctx: RunContext, platform: str, category: str, angles: list[str], seed: str) -> tuple[list[str], dict[str, float]]:
    arms = signals.ad_angle_arms(ctx.session, platform, category, angles)
    rng = signals.rng_for("angles", platform, category, seed)
    draws = {arm.key: arm.sample(rng) for arm in arms}
    return sorted(angles, key=lambda a: draws[a], reverse=True), probability_best(arms, signals.rng_for("pbest", seed))


def write_variant(agent: BaseAgent | None, ctx: RunContext, platform: str, facts: dict[str, Any], angle: str,
                  ai: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(content or None, record of what was rejected and why)."""
    record: dict[str, Any] = {"angle": angle}
    candidate = (ai or {}).get(angle) if isinstance(ai, dict) else None
    if isinstance(candidate, dict) and candidate:
        if platform == "meta":
            candidate = {**adcopy.meta_template(facts, angle), **candidate}
        issues = adcopy.check_variant(platform, candidate, facts)
        if not issues:
            record["source"] = "ai"
            return candidate, record
        record["ai_rejected"] = issues[:10]
    content = adcopy.template(platform, facts, angle)
    issues = adcopy.check_variant(platform, content, facts)
    if issues:
        record["template_rejected"] = issues[:10]
        return None, record
    record["source"] = "template"
    return content, record


def localise_variant(agent: BaseAgent, ctx: RunContext, platform: str, facts: dict[str, Any],
                     content: dict[str, Any], language: str) -> tuple[dict[str, Any] | None, list[str], float]:
    """A checked English ad in Arabic, Turkish or Hebrew: (content or None, why it was rejected, cost).

    The translation must fit the platform limits, pass the claim rules in its
    own language, carry only supported figures, and mean in English (by an
    independent back-translation) nothing the English checks would refuse.
    The English source is kept with the ad so you can read what it says.
    """
    fields = {k: content[k] for k in adcopy.TRANSLATED_FIELDS[platform] if content.get(k)}
    translated, cost = translate_fields(agent, ctx, fields, language, adcopy.TRANSLATION_LIMITS[platform])
    if set(translated) != set(fields) or any(type(translated[k]) is not type(fields[k]) for k in fields):
        return None, ["translation incomplete"], cost
    local = {**content, **translated, "english": fields}
    if platform == "meta" and not languages.is_rtl(language):
        # The generated image card can carry left-to-right scripts; Arabic and Hebrew cards stay
        # in English (upload real photos for those markets).
        local["card"] = {"headline": translated["headline"], "cta": languages.t(language, "card_cta"),
                         "subline": str(translated["primary_text"]).split("\n")[0][:120]}
    issues = adcopy.check_variant(platform, local, facts, language)
    if not issues:
        back, extra = back_translate(agent, ctx, translated, language)
        cost += extra
        text = flatten(back)
        issues = (english_meaning_issues(text, facts["category"], facts, allowed_numbers(facts))
                  if text else ["the translation could not be verified in English"])
    return (None if issues else local), issues, cost


def page_for(ctx: RunContext, category: str, country: str, language: str) -> tuple[LandingPage | None, float, str]:
    page = ensure_page(ctx, category, country, language)
    if page is not None:
        return page, 0.0, ""
    result = LandingPageAgent().run(ctx, {"product_category": category, "country": country, "language": language})
    return ensure_page(ctx, category, country, language), result.cost_usd, result.error or ""


# ------------------------------------------------------------------ plan
class AdPlannerAgent(BaseAgent):
    name = "ad_planner"
    task_type = "ad_copy"
    tier = ModelTier.REASONING
    complexity = 0.7

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        if not ads_active(ctx):
            return self.ok(output={"skipped": "ADS_ENABLED is false"})
        today = ctx.now.date()
        markets = commercial.get(ctx.session)["target_markets"]
        only_cat, only_country = task_input.get("product_category"), task_input.get("country")
        platforms = [p for p in PLATFORMS if (task_input.get("platform") in (None, p)) and platform_for(ctx, p)]
        if not platforms:
            return self.ok(output={"skipped": "no ad platform configured"})

        # Which product lines can be advertised at all: never pharma; must have live offers.
        lines, notes = {}, []
        for category, countries in markets.items():
            if only_cat and category != only_cat:
                continue
            if not ad_allowed(category):
                notes.append(f"{category}: never advertised (R-ADS-01)")
                continue
            if not catalogue.current_offers(ctx.session, category, today):
                notes.append(f"{category}: no live supplier offer, nothing to advertise yet")
                continue
            lines[category] = [c for c in countries if not only_country or c == only_country]

        pool = daily_pool_usd(ctx)
        committed_daily = sum(c.daily_budget_usd for c in ctx.session.scalars(
            select(AdCampaign).where(AdCampaign.status.in_(LIVE_STATUSES))))
        free = pool - committed_daily
        created, total_cost = [], 0.0
        queues: dict[str, list[CountArm]] = {}
        for platform in platforms:
            live = {(c.product_category, (c.countries or [""])[0]) for c in ctx.session.scalars(
                select(AdCampaign).where(AdCampaign.platform == platform, AdCampaign.status.in_(LIVE_STATUSES)))}
            slots = ctx.settings.ads_max_campaigns_per_platform - len(live)
            if slots <= 0:
                notes.append(f"{platform}: already at {ctx.settings.ads_max_campaigns_per_platform} campaigns")
                continue
            # Markets ranked by leads per dollar (learned; prior = target cost per lead).
            arms: list[CountArm] = []
            for category, countries in lines.items():
                for arm in signals.market_arms(ctx.session, category, countries, ctx.settings.ads_target_cost_per_lead_usd):
                    if (category, arm.key) not in live:
                        arms.append(CountArm(f"{category}|{arm.key}", arm.events, arm.exposure, arm.prior_shape, arm.prior_rate))
            rng = signals.rng_for("markets", platform, today.isoformat())
            queues[platform] = sorted(arms, key=lambda a: a.sample(rng), reverse=True)[:slots]
        # Take turns between platforms so one cannot use up the budget before the other starts.
        out_of_budget = False
        while any(queues.values()) and not out_of_budget:
            for platform in list(queues):
                if not queues[platform]:
                    continue
                budget = min(ctx.settings.ads_default_daily_budget_usd, free)
                if budget < ctx.settings.ads_min_daily_budget_usd:
                    notes.append("no room in the ads budget for another campaign this month")
                    out_of_budget = True
                    break
                arm = queues[platform].pop(0)
                category, country = arm.key.split("|", 1)
                campaign, cost, why = self._plan_one(ctx, platform, category, country, round(budget, 2), arm)
                total_cost += cost
                if campaign is None:
                    notes.append(f"{platform} {category} {country}: {why}")
                    continue
                free -= campaign.daily_budget_usd
                created.append(campaign.id)
        next_tasks = [{"agent": "ad_launch", "input": {"campaign_id": cid}, "priority": 80} for cid in created]
        return self.ok(output={"campaigns": created, "notes": notes, "daily_pool_usd": round(pool, 2)},
                       cost_usd=total_cost, next_tasks=next_tasks, notes=notes[:5])

    def _plan_one(self, ctx: RunContext, platform: str, category: str, country: str, budget: float,
                  market_arm: CountArm) -> tuple[AdCampaign | None, float, str]:
        language = languages.language_for(ctx.session, country)
        page, page_cost, error = page_for(ctx, category, country, language)
        if page is None and language != "en":  # no verified page in the local language: advertise in English
            language = "en"
            page, extra, error = page_for(ctx, category, country, language)
            page_cost += extra
        if page is None:
            return None, page_cost, f"landing page could not be published: {error}"
        if not page_url(ctx.settings, page):
            return None, 0.0, "PUBLIC_SITE_URL is not set, so ads would have nowhere to send people"
        facts = page.facts
        angles = adcopy.available_angles(facts)
        if not angles:
            return None, 0.0, "not enough facts (price, lead time, warranty...) to write an honest ad"
        ranked, p_best = rank_angles(ctx, platform, category, angles, f"{country}{ctx.now.date()}")
        chosen = ranked[:VARIANTS_PER_PLATFORM[platform]]
        extra_keywords: list[str] = []
        ai, cost = {}, page_cost
        try:
            data, copy_cost = self.ask(ctx, (
                f"Write {platform} ad copy for B2B buyers, one version per angle in {chosen}, using ONLY the facts. "
                + ("Google: 8-15 headlines of at most 30 characters and 2-4 descriptions of at most 90 characters "
                   "per angle; lead with the angle, include the country and the product, one headline must be a "
                   "call to action. " if platform == "google" else
                   "Meta: primary_text (2-4 short lines, the first line carries the angle and is under 125 "
                   "characters), headline under 40 characters, description under 30, call_to_action GET_QUOTE. ")
                + "No superlatives, no guarantees, no 'new' for used goods, no manufacturer endorsement, no claims "
                "that are not in the facts. Also suggest up to 15 high-intent buyer search keywords. "
                "Return {'variants': {angle: {...}}, 'keywords': []}."
            ), {"facts": facts, "angles": chosen, "platform": platform})
            cost += copy_cost
            ai = data.get("variants") if isinstance(data.get("variants"), dict) else {}
            if platform == "google" and isinstance(data.get("keywords"), list):
                extra_keywords = [str(k) for k in data["keywords"]]
        except Exception as exc:  # noqa: BLE001 - templates are the fallback for any model failure
            ctx.audit.record("ad_copy_model_failed", summary=str(exc)[:300], task_id=ctx.task_id)

        variants, records = [], []
        for angle in chosen:
            content, record = write_variant(self, ctx, platform, facts, angle, ai)
            records.append(record)
            if content is not None:
                variants.append((angle, content))
        minimum = 1 if platform == "google" else 2
        if len(variants) < minimum:
            return None, cost, f"no ad copy passed the checks: {records}"
        if language != "en":
            localised = []
            for angle, content in variants:
                local, issues, extra = localise_variant(self, ctx, platform, facts, content, language)
                cost += extra
                records.append({"angle": angle, "language": language,
                                **({"translation_rejected": issues[:10]} if issues else {"translated": True})})
                if local is not None:
                    localised.append((angle, local))
            if len(localised) >= minimum:
                variants = localised
            else:  # the translations did not pass: run this market in English rather than not at all
                language = "en"
                page, extra, error = page_for(ctx, category, country, language)
                cost += extra
                if page is None:
                    return None, cost, f"landing page could not be published: {error}"
                facts = page.facts
        keywords, negatives, rejected_kw = adcopy.keyword_plan(
            facts, extra_keywords if language == "en" else None, language)

        name = f"{adcopy.NOUN.get(category, category)} - {country} - {platform}"
        campaign = AdCampaign(
            platform=platform, name=name, product_category=category, countries=[country], language=language,
            status="proposed", daily_budget_usd=budget, landing_page_id=page.id,
            targeting={"countries": [country], "keywords": keywords if platform == "google" else [],
                       "negative_keywords": negatives if platform == "google" else [],
                       "audience": "advantage+ (Meta finds the audience; B2B copy filters it)" if platform == "meta" else None},
            plan={"angles_ranked": ranked, "p_best": {k: round(v, 3) for k, v in p_best.items()},
                  "copy": records, "rejected_keywords": rejected_kw[:20], "language": language,
                  "market": {"leads_per_usd_mean": round(market_arm.mean, 4), "observed_leads": market_arm.events,
                             "observed_spend_usd": round(market_arm.exposure, 2)},
                  "budget_rule": "min(default daily budget, room left in the month's ads budget)",
                  "landing_page": page.slug},
        )
        ctx.session.add(campaign)
        ctx.session.flush()
        for i, (angle, content) in enumerate(variants):
            variant = AdVariant(campaign_id=campaign.id, key=f"{angle}-v1", angle=angle, content=content, status="active")
            ctx.session.add(variant)
            ctx.session.flush()
            if platform == "meta":
                variant.asset_id = _variant_image(ctx, campaign, variant, i).id
        ctx.session.flush()
        ctx.audit.record("ad_campaign_planned", summary=f"{name}: {len(variants)} ads, {budget} USD/day",
                         decision="allow", task_id=ctx.task_id, campaign_id=campaign.id)
        return campaign, cost, "planned"


# ------------------------------------------------------------------ launch
def build_launch(ctx: RunContext, campaign: AdCampaign, platform) -> Launch:
    page = ctx.session.get(LandingPage, campaign.landing_page_id)
    variants = []
    for v in ctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id, AdVariant.status == "active")):
        item: dict[str, Any] = {"key": v.key, "angle": v.angle, "content": v.content}
        if v.asset_id:
            asset = ctx.session.get(AdAsset, v.asset_id)
            item["image_bytes"] = asset.data if asset else None
        variants.append(item)
    return Launch(
        campaign_id=campaign.id, name=campaign.name, countries=list(campaign.countries or []), language=campaign.language,
        daily_budget_native=fx_to_native(ctx, campaign.daily_budget_usd, platform.currency),
        landing_url=page_url(ctx.settings, page) if page else "", url_suffix=url_suffix(campaign.platform, campaign),
        keywords=(campaign.targeting or {}).get("keywords") or [],
        negative_keywords=(campaign.targeting or {}).get("negative_keywords") or [],
        variants=variants, business_name=ctx.settings.business_name or "NEXUS Sourcing",
        use_conversions=bool(ctx.settings.google_ads_conversion_action_lead) and campaign.platform == "google",
    )


def recheck(ctx: RunContext, campaign: AdCampaign) -> list[str]:
    """Re-run every copy check at launch time (facts may have changed since planning)."""
    page = ctx.session.get(LandingPage, campaign.landing_page_id)
    if page is None or page.status != "published":
        return ["landing page is not published"]
    issues = []
    for v in ctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id, AdVariant.status == "active")):
        issues += [f"{v.key}: {i}" for i in adcopy.check_variant(campaign.platform, v.content, page.facts,
                                                                    campaign.language or "en")]
    return issues


def ad_preview(ctx: RunContext, campaign: AdCampaign) -> str:
    lines = [f"{campaign.name}: {campaign.daily_budget_usd} USD/day, {', '.join(campaign.countries or [])}"]
    if (campaign.language or "en") != "en":
        lines.append(f"Ads run in {languages.language_name(campaign.language)}; shown here is the English they were "
                     "translated from (the translation was checked back into English).")
    for v in ctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id)):
        c = {**v.content, **(v.content.get("english") or {})}
        if campaign.platform == "google":
            lines.append(f"[{v.angle}] " + " | ".join(c.get("headlines", [])[:5]) + " — " + (c.get("descriptions") or [""])[0])
        else:
            lines.append(f"[{v.angle}] {c.get('headline')}: {c.get('primary_text', '')[:160]}")
    kws = (campaign.targeting or {}).get("keywords") or []
    if kws:
        lines.append("Keywords: " + ", ".join(kws[:12]))
    return "\n".join(lines)


class AdLaunchAgent(BaseAgent):
    name = "ad_launch"
    task_type = "ad_launch"
    tier = ModelTier.BULK

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        campaign = ctx.session.get(AdCampaign, task_input["campaign_id"])
        if campaign is None:
            return self.fail("campaign not found")
        if campaign.status not in ("proposed", "awaiting_approval"):
            return self.ok(output={"skipped": f"campaign is {campaign.status}"})
        request = ActionRequest(
            kind=ActionKind.LAUNCH_AD_CAMPAIGN,
            summary=f"launch {campaign.platform} campaign {campaign.name}",
            payload={"campaign_id": campaign.id, "platform": campaign.platform, "product_category": campaign.product_category,
                     "country": (campaign.countries or [None])[0], "daily_budget_usd": campaign.daily_budget_usd,
                     "compliance_issues": recheck(ctx, campaign), "ad_preview": ad_preview(ctx, campaign),
                     "approved_review_id": task_input.get("approved_review_id")},
            estimated_cost_usd=round(campaign.daily_budget_usd * 7, 2),  # a week of spend must fit
            cost_category="ads",
            idempotency_key=stable_key("ad_launch", campaign.id),
        )
        decision = ctx.authorize(request)
        if decision.decision == Decision.ESCALATE:
            campaign.status = "awaiting_approval"
            return self.ok(output={"awaiting_approval": True}, notes=["campaign waits for your approval"])
        if decision.decision == Decision.BLOCK:
            campaign.status = "blocked"
            campaign.status_reason = "; ".join(decision.reasons)[:2000]
            return self.ok(output={"blocked": decision.reasons})
        platform = platform_for(ctx, campaign.platform)
        if platform is None:
            campaign.status = "failed"
            campaign.status_reason = f"{campaign.platform} is not configured"
            return self.fail(campaign.status_reason)
        try:
            external = platform.create(build_launch(ctx, campaign, platform))
        except AdPlatformError as exc:
            from app.notify import notify

            campaign.status = "failed"
            campaign.status_reason = str(exc)[:2000]
            notify(ctx, f"Ad campaign failed to launch: {campaign.name}", str(exc)[:1500], dedupe_key=f"adfail:{campaign.id}")
            return self.fail(str(exc))
        campaign.external_ids = external
        campaign.status = "active"
        campaign.launched_at = ctx.now
        for v in ctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id)):
            v.external_id = str((external.get("variants") or {}).get(v.key) or "") or None
        ctx.session.flush()
        ctx.audit.record("ad_campaign_launched", summary=campaign.name, decision="allow", task_id=ctx.task_id,
                         campaign_id=campaign.id, external=external)
        return self.ok(output={"launched": True, "external": external}, notes=[f"{campaign.name} is live"])


# ------------------------------------------------------------------ sync
def _simulate_leads(ctx: RunContext, platform: SimulatedAdsPlatform, campaign: AdCampaign, variant: AdVariant,
                    new_clicks: int, day: date) -> int:
    """Simulation only: some clicks become enquiries, through the real intake path."""
    from app.site.leads import intake

    page = ctx.session.get(LandingPage, campaign.landing_page_id)
    if page is None or new_clicks <= 0:
        return 0
    n = platform.simulated_leads(campaign.external_ids, variant.external_id or "", new_clicks, day)
    for i in range(n):
        tag = stable_key("simlead", campaign.id, variant.key, day.isoformat(), i)[:10]
        form = {"full_name": f"Buyer {tag[:4].upper()}", "organisation": f"Simulated Buyer {tag[:6]} Ltd",
                "email": f"procurement@buyer-{tag}.example", "quantity": "25", "consent": "yes",
                "message": "Please send a quotation.", "nx": campaign.id, "nv": variant.external_id or variant.key,
                "gclid" if campaign.platform == "google" else "fbclid": f"sim-{tag}"}
        intake(ctx, page, form, visitor=stable_key("simvisitor", tag))
    return n


def ads_sync(ctx: RunContext, lookback_days: int = 3) -> dict:
    """Pull metrics, book spend against the budget, and stop everything at the limit."""
    synced, spend_booked, leads_simulated, errors = 0, 0.0, 0, []
    today = ctx.now.date()
    campaigns = list(ctx.session.scalars(select(AdCampaign).where(
        AdCampaign.status.in_(("active", "paused_budget", "paused_no_leads", "paused")),
        AdCampaign.launched_at.is_not(None))))
    for campaign in campaigns:
        platform = platform_for(ctx, campaign.platform)
        if platform is None:
            continue
        since = max(campaign.launched_at.date(), today - timedelta(days=lookback_days))
        try:
            rows = platform.metrics(campaign.external_ids, since, today)
        except AdPlatformError as exc:
            errors.append(f"{campaign.name}: {exc}")
            continue
        by_external = {v.external_id: v for v in ctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id))}
        for row in rows:
            variant = by_external.get(row["variant_external_id"])
            key = variant.key if variant else f"ext:{row['variant_external_id']}"
            metric = ctx.session.scalar(select(AdMetric).where(AdMetric.campaign_id == campaign.id,
                                                              AdMetric.variant_key == key, AdMetric.day == row["day"]))
            if metric is None:
                metric = AdMetric(campaign_id=campaign.id, variant_key=key, day=row["day"], currency=platform.currency)
                ctx.session.add(metric)
            new_clicks = row["clicks"] - (metric.clicks or 0)
            metric.impressions, metric.clicks = row["impressions"], row["clicks"]
            metric.spend_native = row["spend_native"]
            try:
                metric.spend_usd = fx_to_usd(ctx, row["spend_native"], platform.currency)
            except AdPlatformError as exc:
                errors.append(str(exc))
                continue
            ctx.session.flush()
            delta = round(metric.spend_usd - (metric.ledgered_usd or 0.0), 4)
            if delta > 0:
                ctx.budget.record_incurred("ads", delta, reason=f"{campaign.platform} spend {row['day']}", reference=metric.id)
                metric.ledgered_usd = round((metric.ledgered_usd or 0.0) + delta, 4)
                spend_booked += delta
            if isinstance(platform, SimulatedAdsPlatform) and variant is not None:
                leads_simulated += _simulate_leads(ctx, platform, campaign, variant, new_clicks, row["day"])
        campaign.last_synced_at = ctx.now
        synced += 1
    guard = budget_guard(ctx)
    ctx.session.flush()
    return {"campaigns_synced": synced, "spend_booked_usd": round(spend_booked, 2),
            "simulated_leads": leads_simulated, "errors": errors[:10], **guard}


def budget_guard(ctx: RunContext) -> dict:
    """Pause every campaign at 95% of the ads budget (or a hard stop); resume when money is back."""
    limit = ctx.budget.category_limit("ads") or 0.0
    used = limit - ctx.budget.category_remaining("ads") if limit else 0.0
    exhausted = ctx.budget.hard_stopped() or (limit and used >= 0.95 * limit)
    paused = resumed = 0
    target_from, target_to = ("active", "paused_budget") if exhausted else ("paused_budget", "active")
    for campaign in ctx.session.scalars(select(AdCampaign).where(AdCampaign.status == target_from)):
        platform = platform_for(ctx, campaign.platform)
        if platform is None:
            continue
        try:
            platform.set_status(campaign.external_ids, active=not exhausted)
        except AdPlatformError as exc:
            ctx.audit.record("ad_status_failed", summary=f"{campaign.name}: {exc}", decision="block")
            continue
        campaign.status = target_to
        campaign.status_reason = "ads budget reached" if exhausted else None
        paused += int(exhausted)
        resumed += int(not exhausted)
    if paused:
        from app.notify import notify

        notify(ctx, "Ads paused: monthly ads budget reached",
               f"{paused} campaign(s) paused at {used:.2f} of {limit:.2f} USD. They resume automatically next month "
               "or when you raise the ads budget.", dedupe_key=f"adsbudget:{ctx.now:%Y-%m}")
    return {"paused_for_budget": paused, "resumed": resumed}


# ------------------------------------------------------------------ optimise
class AdOptimizerAgent(BaseAgent):
    name = "ad_optimizer"
    task_type = "ad_copy"
    tier = ModelTier.REASONING

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        campaign = ctx.session.get(AdCampaign, task_input["campaign_id"])
        if campaign is None or campaign.status != "active":
            return self.ok(output={"skipped": True})
        platform = platform_for(ctx, campaign.platform)
        if platform is None:
            return self.ok(output={"skipped": "platform not configured"})
        actions: list[str] = []
        stats = signals.variant_stats(ctx.session, campaign.id)
        totals = signals.campaign_totals(ctx.session, campaign.id)
        target = ctx.settings.ads_target_cost_per_lead_usd

        # 1. Spending without enquiries: stop.
        if totals["leads"] == 0 and totals["spend_usd"] >= max(30.0, 3 * target):
            platform.set_status(campaign.external_ids, active=False)
            campaign.status = "paused_no_leads"
            campaign.status_reason = f"{totals['spend_usd']:.2f} USD spent, {totals['clicks']} clicks, no enquiries"
            from app.notify import notify

            notify(ctx, f"Ad campaign paused: {campaign.name}", campaign.status_reason + ". Check the landing page and "
                   "the offer; resume it from the dashboard when fixed.", dedupe_key=f"adnoleads:{campaign.id}")
            campaign.last_optimized_at = ctx.now
            return self.ok(output={"paused": campaign.status_reason}, notes=[campaign.status_reason])

        # 2. Prune losing ads once each has enough clicks to judge; write a replacement from the next-best angle.
        a, b = signals.lead_rate_prior()
        active = list(ctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id, AdVariant.status == "active")))
        arms = [RateArm(v.key, stats.get(v.key, {}).get("leads", 0), max(stats.get(v.key, {}).get("clicks", 0),
                        stats.get(v.key, {}).get("leads", 0)), a, b) for v in active]
        p_best = probability_best(arms, signals.rng_for("prune", campaign.id, ctx.now.date()))
        cost = 0.0
        for v in active:
            clicks = stats.get(v.key, {}).get("clicks", 0)
            if (len([x for x in active if x.status == "active"]) > 2 and clicks >= ctx.settings.ads_prune_min_clicks
                    and p_best.get(v.key, 1.0) < PRUNE_PROB_BEST):
                if v.external_id:
                    platform.set_variant_status(campaign.external_ids, v.external_id, active=False)
                v.status = "paused"
                v.status_reason = f"P(best)={p_best[v.key]:.3f} after {clicks} clicks"
                actions.append(f"paused ad {v.key} ({v.status_reason})")
                replacement, c = self._replacement(ctx, campaign, platform)
                cost += c
                if replacement:
                    actions.append(f"added ad {replacement}")

        # 3. Expensive leads: trim the budget (bounded); the portfolio step may move it back.
        if totals["leads"] >= 3 and totals["spend_usd"] / totals["leads"] > 3 * target:
            new = max(ctx.settings.ads_min_daily_budget_usd, round(campaign.daily_budget_usd * (1 - MAX_DAILY_CHANGE), 2))
            if new < campaign.daily_budget_usd:
                platform.set_budget(campaign.external_ids, fx_to_native(ctx, new, platform.currency))
                actions.append(f"budget {campaign.daily_budget_usd} -> {new} USD/day (cost per enquiry "
                               f"{totals['spend_usd'] / totals['leads']:.2f} vs target {target})")
                campaign.daily_budget_usd = new

        # 4. Search terms that waste money become negatives (Google).
        if campaign.platform == "google":
            added = self._negatives(ctx, campaign, platform)
            if added:
                actions.append(f"blocked search terms: {', '.join(added[:8])}")

        campaign.last_optimized_at = ctx.now
        ctx.audit.record("ad_campaign_optimized", summary=f"{campaign.name}: {len(actions)} change(s)", decision="allow",
                         task_id=ctx.task_id, campaign_id=campaign.id, actions=actions, totals=totals,
                         p_best={k: round(v, 3) for k, v in p_best.items()})
        return self.ok(output={"actions": actions, "totals": totals}, cost_usd=cost, notes=actions[:5])

    def _replacement(self, ctx: RunContext, campaign: AdCampaign, platform) -> tuple[str | None, float]:
        page = ctx.session.get(LandingPage, campaign.landing_page_id)
        if page is None:
            return None, 0.0
        variants = list(ctx.session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id)))
        used = {v.angle for v in variants}  # a losing angle is not retried in the same campaign
        candidates = [a for a in adcopy.available_angles(page.facts) if a not in used]
        if not candidates:
            return None, 0.0
        ranked, _ = rank_angles(ctx, campaign.platform, campaign.product_category, candidates, f"{campaign.id}{ctx.now.date()}")
        angle = ranked[0]
        content, _record = write_variant(self, ctx, campaign.platform, page.facts, angle, None)
        if content is None:
            return None, 0.0
        translation_cost = 0.0
        if (campaign.language or "en") != "en":
            content, _issues, translation_cost = localise_variant(self, ctx, campaign.platform, page.facts, content,
                                                                  campaign.language)
            if content is None:
                return None, translation_cost
        version = 1 + sum(1 for v in variants if v.angle == angle)
        variant = AdVariant(campaign_id=campaign.id, key=f"{angle}-v{version}", angle=angle, content=content, status="active")
        ctx.session.add(variant)
        ctx.session.flush()
        item: dict[str, Any] = {"key": variant.key, "angle": angle, "content": content}
        if campaign.platform == "meta":
            asset = _variant_image(ctx, campaign, variant, len(variants))
            variant.asset_id = asset.id
            item["image_bytes"] = asset.data
        try:
            variant.external_id = platform.add_variant(campaign.external_ids, build_launch(ctx, campaign, platform), item)
        except AdPlatformError as exc:
            variant.status = "failed"
            variant.status_reason = str(exc)[:1000]
            return None, 0.0
        return variant.key, translation_cost

    @staticmethod
    def _negatives(ctx: RunContext, campaign: AdCampaign, platform) -> list[str]:
        today = ctx.now.date()
        terms = platform.search_terms(campaign.external_ids, today - timedelta(days=14), today)
        existing = set((campaign.targeting or {}).get("negative_keywords") or [])
        keywords = (campaign.targeting or {}).get("keywords") or []
        vocab = {w for k in keywords for w in k.split() if len(w) > 3}
        bank = adcopy.NEGATIVES_COMMON + adcopy.NEGATIVES.get(campaign.product_category, [])
        add = []
        for t in terms:
            term = (t.get("term") or "").lower().strip()
            if not term or term in existing:
                continue
            words = set(term.split())
            wasteful = any(n in words or (" " in n and n in term) for n in bank)
            off_topic = t.get("clicks", 0) >= 20 and not (words & vocab)
            if wasteful or off_topic:
                add.append(term)
        if add:
            platform.add_negative_keywords(campaign.external_ids, add)
            targeting = dict(campaign.targeting or {})
            targeting["negative_keywords"] = sorted(existing | set(add))
            campaign.targeting = targeting
        return add


def reallocate_budgets(ctx: RunContext) -> dict:
    """Move daily budget toward campaigns that bring enquiries (Thompson sampling, bounded)."""
    campaigns = list(ctx.session.scalars(select(AdCampaign).where(AdCampaign.status == "active")))
    if len(campaigns) < 2:
        return {"reallocated": 0}
    since = ctx.now.date() - timedelta(days=14)
    target = ctx.settings.ads_target_cost_per_lead_usd
    arms = []
    for c in campaigns:
        t = signals.campaign_totals(ctx.session, c.id, since)
        arms.append(CountArm(c.id, events=t["leads"] + 2.0 * t["won"], exposure=t["spend_usd"], prior_shape=1.0, prior_rate=target))
    pool = min(sum(c.daily_budget_usd for c in campaigns), daily_pool_usd(ctx))
    shares = thompson_allocation(arms, pool, signals.rng_for("realloc", ctx.now.date()))
    minimum = ctx.settings.ads_min_daily_budget_usd
    changes = []
    for c in campaigns:
        wanted = shares.get(c.id, c.daily_budget_usd)
        low, high = c.daily_budget_usd * (1 - MAX_DAILY_CHANGE), c.daily_budget_usd * (1 + MAX_DAILY_CHANGE)
        new = round(max(minimum, min(high, max(low, wanted))), 2)
        if abs(new - c.daily_budget_usd) < 0.05:
            continue
        platform = platform_for(ctx, c.platform)
        if platform is None:
            continue
        try:
            platform.set_budget(c.external_ids, fx_to_native(ctx, new, platform.currency))
        except AdPlatformError as exc:
            ctx.audit.record("ad_budget_failed", summary=f"{c.name}: {exc}", decision="block")
            continue
        changes.append({"campaign": c.name, "from": c.daily_budget_usd, "to": new})
        c.daily_budget_usd = new
    if changes:
        ctx.audit.record("ad_budgets_reallocated", summary=f"{len(changes)} budget change(s)", decision="allow", changes=changes)
    return {"reallocated": len(changes), "changes": changes}


# ------------------------------------------------------------------ conversions
class ConversionUploadAgent(BaseAgent):
    """Tell Google/Meta which ad clicks became enquiries and sales (offline conversions)."""

    name = "conversion_upload"
    task_type = "conversion_upload"
    tier = ModelTier.BULK

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        lead = ctx.session.get(Lead, task_input["lead_id"])
        event = task_input.get("event", "lead")
        if lead is None or lead.platform not in PLATFORMS or lead.status == "spam":
            return self.ok(output={"skipped": True})
        if (lead.conversions_sent or {}).get(event):
            return self.ok(output={"already_sent": True})
        platform = platform_for(ctx, lead.platform)
        if platform is None:
            return self.ok(output={"skipped": f"{lead.platform} not configured"})
        page = ctx.session.get(LandingPage, lead.landing_page_id) if lead.landing_page_id else None
        try:
            sent = platform.upload_conversion(event, lead.attribution or {}, lead.email, lead.phone,
                                              task_input.get("value_usd"), ctx.now if event == "won" else lead.created_at,
                                              stable_key("conv", lead.id, event), page_url(ctx.settings, page) if page else None)
        except AdPlatformError as exc:
            return self.fail(str(exc))
        if sent:
            lead.conversions_sent = {**(lead.conversions_sent or {}), event: ctx.now.isoformat()}
        return self.ok(output={"sent": sent}, notes=[f"{event} conversion {'sent' if sent else 'not sendable'} to {lead.platform}"])


# ------------------------------------------------------------------ jobs
def ads_plan_job(ctx: RunContext) -> dict:
    if not ads_active(ctx):
        return {"skipped": "ads disabled"}
    _, created = ctx.tasks.create_task(agent="ad_planner", objective_id=None, task_input={}, priority=60,
                                       idempotency_key=stable_key("ad_planner", ctx.now.strftime("%Y-%W")))
    return {"tasks_created": int(created)}


def ads_optimize_job(ctx: RunContext) -> dict:
    if not ads_active(ctx):
        return {"skipped": "ads disabled"}
    created = 0
    for c in ctx.session.scalars(select(AdCampaign).where(AdCampaign.status == "active")):
        if c.launched_at and (ctx.now - c.launched_at.replace(tzinfo=ctx.now.tzinfo)) < timedelta(days=3):
            continue  # let the platform's own learning settle first
        _, new = ctx.tasks.create_task(agent="ad_optimizer", objective_id=None, task_input={"campaign_id": c.id},
                                       priority=65, idempotency_key=stable_key("ad_optimizer", c.id, ctx.now.date()))
        created += int(new)
    return {"tasks_created": created, **reallocate_budgets(ctx)}
