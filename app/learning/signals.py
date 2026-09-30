"""Observed outcomes turned into bandit arms: what NEXUS has actually seen work.

Every learner in NEXUS reads its evidence from here, so there is one
definition of "a lead", "a positive reply" and "a win":

  lead      an enquiry from a landing page that is not spam
  positive  an inbound reply classified interested / information / price / RFQ / negotiation
  won       an opportunity closed as won
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.ids import stable_key
from app.database.models import AdCampaign, AdMetric, AdVariant, Interaction, Lead, Message, Opportunity
from app.learning.bandits import CountArm, RateArm

POSITIVE = ("interested", "information_request", "price_request", "rfq", "negotiation")
EXPECTED_LEAD_RATE = 0.03  # prior belief: 3 enquiries per 100 clicks
PRIOR_CLICKS = 30.0        # ...held about as strongly as 30 clicks of evidence


def rng_for(*parts: Any) -> random.Random:
    """Reproducible randomness per decision (same inputs, same draw)."""
    return random.Random(int(stable_key("rng", *parts)[:12], 16))


def lead_rate_prior() -> tuple[float, float]:
    a = EXPECTED_LEAD_RATE * PRIOR_CLICKS
    return a, PRIOR_CLICKS - a


# ------------------------------------------------------------------ ads
def variant_stats(session: Session, campaign_id: str, since: date | None = None) -> dict[str, dict[str, float]]:
    """Per variant key: impressions, clicks, spend, leads, wins (lifetime unless since)."""
    stats: dict[str, dict[str, float]] = defaultdict(lambda: {"impressions": 0, "clicks": 0, "spend_usd": 0.0,
                                                               "leads": 0, "won": 0})
    q = select(AdMetric).where(AdMetric.campaign_id == campaign_id)
    if since:
        q = q.where(AdMetric.day >= since)
    for row in session.scalars(q):
        s = stats[row.variant_key]
        s["impressions"] += row.impressions
        s["clicks"] += row.clicks
        s["spend_usd"] += row.spend_usd
    lq = select(Lead).where(Lead.campaign_id == campaign_id, Lead.status != "spam")
    if since:
        lq = lq.where(Lead.created_at >= datetime.combine(since, datetime.min.time()))
    for lead in session.scalars(lq):
        s = stats[lead.variant_key or "_unattributed"]
        s["leads"] += 1
        s["won"] += int(lead.status == "won")
    return dict(stats)


def campaign_totals(session: Session, campaign_id: str, since: date | None = None) -> dict[str, float]:
    total = {"impressions": 0, "clicks": 0, "spend_usd": 0.0, "leads": 0, "won": 0}
    for s in variant_stats(session, campaign_id, since).values():
        for k in total:
            total[k] += s[k]
    return total


def ad_angle_arms(session: Session, platform: str, category: str, angles: Iterable[str]) -> list[RateArm]:
    """Lead rate per ad angle, pooled over every campaign for this platform and product line."""
    a, b = lead_rate_prior()
    clicks: dict[str, float] = defaultdict(float)
    leads: dict[str, float] = defaultdict(float)
    campaigns = list(session.scalars(select(AdCampaign).where(AdCampaign.platform == platform,
                                                              AdCampaign.product_category == category)))
    for campaign in campaigns:
        angle_of = {v.key: v.angle for v in session.scalars(select(AdVariant).where(AdVariant.campaign_id == campaign.id))}
        for key, s in variant_stats(session, campaign.id).items():
            angle = angle_of.get(key)
            if angle:
                clicks[angle] += s["clicks"]
                leads[angle] += s["leads"]
    return [RateArm(angle, successes=leads[angle], trials=max(clicks[angle], leads[angle]), prior_a=a, prior_b=b)
            for angle in angles]


def market_arms(session: Session, category: str, countries: Iterable[str], target_cpl: float) -> list[CountArm]:
    """Leads per dollar by country, across both platforms. Prior mean = 1 / target cost per lead."""
    spend: dict[str, float] = defaultdict(float)
    leads: dict[str, float] = defaultdict(float)
    for campaign in session.scalars(select(AdCampaign).where(AdCampaign.product_category == category)):
        totals = campaign_totals(session, campaign.id)
        countries_here = campaign.countries or []
        for country in countries_here:
            spend[country] += totals["spend_usd"] / max(len(countries_here), 1)
            leads[country] += totals["leads"] / max(len(countries_here), 1)
    # Organic enquiries count too: they show demand exists in that market.
    for country, n in session.execute(select(Lead.country, func.count()).where(
            Lead.product_category == category, Lead.platform == "organic", Lead.status != "spam").group_by(Lead.country)):
        if country:
            leads[country] += 0.5 * n
    return [CountArm(c, events=leads[c], exposure=spend[c], prior_shape=1.0, prior_rate=max(target_cpl, 1.0))
            for c in countries]


# ------------------------------------------------------------------ e-mail
def message_arms(session: Session, category: str, dimension: str, options: Iterable[str]) -> list[RateArm]:
    """Positive-reply rate per value of Message.variant[dimension] (e.g. angle, subject style)."""
    sent: dict[str, float] = defaultdict(float)
    positive: dict[str, float] = defaultdict(float)
    rows = session.execute(
        select(Message.id, Message.variant, Message.opportunity_id)
        .join(Opportunity, Opportunity.id == Message.opportunity_id)
        .where(Message.direction == "outbound", Message.status == "sent",
               Opportunity.product_category == category, Message.sequence_step == 0)
    ).all()
    opp_value: dict[str, str] = {}
    for _mid, variant, opp_id in rows:
        value = (variant or {}).get(dimension)
        if not value:
            continue  # first-touch messages only: follow-ups inherit the first message's choice
        sent[value] += 1
        opp_value[opp_id] = value
    if opp_value:
        replied = session.scalars(select(Interaction.opportunity_id).where(
            Interaction.direction == "inbound", Interaction.category.in_(POSITIVE),
            Interaction.opportunity_id.in_(list(opp_value)))).all()
        for opp_id in set(replied):
            positive[opp_value[opp_id]] += 1
    # Weak prior: about a 4% positive-reply rate, worth 25 sends.
    return [RateArm(o, successes=positive[o], trials=max(sent[o], positive[o]), prior_a=1.0, prior_b=24.0) for o in options]


def segment_arms(session: Session, keys: Iterable[tuple[str, str]]) -> list[RateArm]:
    """Positive-reply rate per (product line, country) for outbound deals."""
    sent: dict[tuple[str, str], float] = defaultdict(float)
    positive: dict[tuple[str, str], float] = defaultdict(float)
    from app.database.models import Company

    rows = session.execute(
        select(Opportunity.id, Opportunity.product_category, Company.country)
        .join(Company, Company.id == Opportunity.company_id)
        .join(Message, Message.opportunity_id == Opportunity.id)
        .where(Message.direction == "outbound", Message.status == "sent")
        .distinct()
    ).all()
    key_of = {}
    for opp_id, category, country in rows:
        key_of[opp_id] = (category, country or "unknown")
        sent[key_of[opp_id]] += 1
    if key_of:
        for opp_id in set(session.scalars(select(Interaction.opportunity_id).where(
                Interaction.direction == "inbound", Interaction.category.in_(POSITIVE),
                Interaction.opportunity_id.in_(list(key_of))))):
            positive[key_of[opp_id]] += 1
    return [RateArm(f"{k[0]}|{k[1]}", successes=positive[k], trials=max(sent[k], positive[k]), prior_a=1.0, prior_b=24.0)
            for k in keys]
