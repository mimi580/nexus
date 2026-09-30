"""The self-improvement loop: learners pick what worked, scoring refits and rolls back on evidence."""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import select

from app.agents.learning import LearningAgent
from app.agents.qualification import OpportunityScoringAgent
from app.core.types import OpportunityStage, ProductCategory
from app.database.models import Interaction, Message, Strategy, SystemState
from app.learning import loop, signals
from app.learning.bandits import RateArm, probability_best, thompson_allocation

LAPTOP = ProductCategory.LAPTOP.value
KEYS = ["buyer_probability", "product_fit", "order_size", "margin", "supplier_availability",
        "market_attractiveness", "payment_risk", "regulatory_risk", "time_to_close"]


def contacted_deal(ctx, i, *, components, engaged, variant=None, country="Kenya", sent_days_ago=30, category=LAPTOP):
    company, _ = ctx.memory.upsert_company(name=f"Buyer {i} {country}", domain=f"buyer{i}{country.lower()}.test", country=country)
    contact = ctx.memory.upsert_contact(company_id=company.id, full_name="Ann Buyer", role="Procurement",
                                        email=f"ann{i}@buyer{i}{country.lower()}.test", confidence=0.8, source="test")
    opp, _ = ctx.memory.create_opportunity(company.id, category)
    opp.contact_id = contact.id
    opp.score_breakdown = {"components": components}
    sent_at = ctx.now - timedelta(days=sent_days_ago)
    ctx.session.add(Message(opportunity_id=opp.id, contact_id=contact.id, direction="outbound", status="sent",
                            sequence_step=0, sent_at=sent_at, dedupe_key=f"m{i}{country}", body="hi", variant=variant or {}))
    if engaged:
        ctx.session.add(Interaction(opportunity_id=opp.id, company_id=company.id, direction="inbound", category="interested"))
    ctx.session.flush()
    return opp


def random_components(rng):
    return {k: round(rng.random(), 3) for k in KEYS}


def test_bandit_basics_prefer_the_better_arm():
    rng = random.Random(1)
    good, bad = RateArm("good", 30, 100), RateArm("bad", 3, 100)
    assert probability_best([good, bad], rng)["good"] > 0.99
    split = thompson_allocation([good, bad], 10.0, rng)
    assert split["good"] > 9.5 and abs(sum(split.values()) - 10.0) < 1e-9


def test_email_variant_learns_from_replies(ctx):
    for i in range(60):
        angle = "availability" if i % 2 else "cost"
        engaged = angle == "availability" and i % 3 != 0
        contacted_deal(ctx, i, components=random_components(random.Random(i)), engaged=engaged,
                       variant={"angle": angle, "subject_style": "direct"})
    picks = [loop.choose_email_variant(ctx.session, LAPTOP, f"s{i}", regulated=False)["angle"] for i in range(40)]
    assert picks.count("availability") > 30
    assert "licensed" not in picks  # never offered for unregulated lines
    arms = {a.key: a for a in signals.message_arms(ctx.session, LAPTOP, "angle", list(loop.EMAIL_ANGLES))}
    assert arms["availability"].trials == 30 and arms["availability"].successes == 20


def test_outreach_records_the_variant_it_used(ctx):
    from app.agents.outreach import OutreachAgent
    from app.agents.sourcing import SalesStrategyAgent

    company, _ = ctx.memory.upsert_company(name="Variant Co", domain="variant.test", country="Kenya")
    contact = ctx.memory.upsert_contact(company_id=company.id, full_name="Joy Kamau", role="Procurement Manager",
                                        email="joy@variant.test", confidence=0.8, source="directory")
    opp, _ = ctx.memory.create_opportunity(company.id, LAPTOP)
    opp.contact_id = contact.id
    opp.economics = {"quantity": 20}
    ctx.session.flush()
    result = SalesStrategyAgent().run(ctx, {"opportunity_id": opp.id})
    variant = opp.qualification["strategy"]["variant"]
    assert variant["angle"] in loop.EMAIL_ANGLES and 50 <= result.next_tasks[0]["priority"] <= 60
    OutreachAgent().run(ctx, {"opportunity_id": opp.id, "regulatory_checked": True})
    message = ctx.session.scalar(select(Message).where(Message.opportunity_id == opp.id, Message.direction == "outbound"))
    assert message.status == "sent" and message.variant["angle"] == variant["angle"]


def test_market_score_blends_in_observed_results_only_with_enough_data(ctx):
    assert loop.learned_market_score(ctx.session, LAPTOP, "Kenya", 0.4) == 0.4
    for i in range(40):
        contacted_deal(ctx, i, components=random_components(random.Random(i)), engaged=i % 4 == 0)
    blended = loop.learned_market_score(ctx.session, LAPTOP, "Kenya", 0.4)
    assert blended > 0.4 and blended < 1.0


def test_supplier_regions_that_quote_are_searched_first(ctx):
    for i in range(12):
        region = "India" if i % 2 else "China"
        company, _ = ctx.memory.upsert_company(name=f"Supplier {i}", domain=f"sup{i}.test", country=region, kind="supplier",
                                              profile={"categories": [LAPTOP], "source_region": region})
        company.profile = {"categories": [LAPTOP], "source_region": region}
        company.status = "quoted" if region == "India" else "unresponsive"
    ctx.session.flush()
    firsts = [loop.rank_supplier_regions(ctx.session, LAPTOP, ["China", "India", "Kenya"], 2, f"w{i}")[0] for i in range(20)]
    assert firsts.count("India") >= 15


def build_labelled_history(ctx, n=80, seed=3, offset=0, days_ago=40, invert=False):
    """Engagement driven by buyer_probability and order_size; margin is noise."""
    rng = random.Random(seed)
    for i in range(n):
        c = random_components(rng)
        signal = c["buyer_probability"] + c["order_size"]
        engaged = (signal < 0.9) if invert else (signal > 1.1)
        contacted_deal(ctx, offset + i, components=c, engaged=engaged, sent_days_ago=days_ago)


def test_scoring_waits_for_enough_labels(ctx):
    build_labelled_history(ctx, n=10)
    report = loop.calibrate_scoring(ctx)
    assert "waiting for data" in report["decision"] and loop.active_scoring(ctx.session) is None


def test_scoring_refit_activates_only_when_it_ranks_better(ctx):
    build_labelled_history(ctx)
    report = loop.calibrate_scoring(ctx)
    assert report["model_cv_auc"] > report["baseline_auc"]
    strategy = loop.active_scoring(ctx.session)
    assert strategy is not None and strategy.version == 1, report
    new, old = strategy.parameters["weights"], strategy.parameters["previous_weights"]
    assert new["buyer_probability"] > old["buyer_probability"]
    assert all(abs(new[k] - old[k]) <= loop.MAX_WEIGHT_STEP + 1e-6 for k in KEYS)  # bounded step

    # The scorer now uses the learned weights, and says which version it used.
    opp = contacted_deal(ctx, 999, components=random_components(random.Random(9)), engaged=False, sent_days_ago=1)
    opp.qualification = {"need_evidence": 0.9, "decision_maker_confidence": 0.8}
    OpportunityScoringAgent().run(ctx, {"opportunity_id": opp.id})
    assert opp.score_breakdown["weights_version"] == 1 and opp.score_breakdown["weights"] == new


def test_scoring_rolls_back_when_live_results_disagree(ctx, clock):
    build_labelled_history(ctx)
    loop.calibrate_scoring(ctx)
    active = loop.active_scoring(ctx.session)
    assert active is not None
    clock.advance(days=30)
    # After activation the world flips: the features the refit favoured now predict silence.
    build_labelled_history(ctx, n=60, seed=11, offset=5000, days_ago=22, invert=True)
    result = loop.check_rollback(ctx)
    assert result["rollback"] == "rolled back"
    assert loop.active_scoring(ctx.session) is None  # back to the defaults
    assert loop.active_weights(ctx.session) == loop.default_weights()


def test_operator_rollback_restores_previous_version(ctx, clock):
    build_labelled_history(ctx)
    loop.calibrate_scoring(ctx)
    loop.rollback_scoring(ctx, reason="test", actor="operator")
    assert loop.active_scoring(ctx.session) is None
    assert ctx.session.scalar(select(Strategy).where(Strategy.name == loop.SCORING_STRATEGY)).active is False


def test_learning_agent_writes_the_snapshot(ctx):
    build_labelled_history(ctx, n=40)
    result = LearningAgent().run(ctx, {"window_days": 60})
    assert result.ok and "decision" in result.output["calibration"]
    snap = ctx.session.get(SystemState, "learning_snapshot").value
    assert set(snap) >= {"email", "ads", "suppliers", "scoring", "metrics", "calibration"}
    assert "pharmaceutical" not in snap["ads"]


def test_labels_ignore_inbound_and_uncontacted_deals(ctx):
    opp = contacted_deal(ctx, 1, components=random_components(random.Random(1)), engaged=True)
    opp.qualification = {"inbound": True}
    contacted_deal(ctx, 2, components=random_components(random.Random(2)), engaged=False, sent_days_ago=5)  # too recent
    lost = contacted_deal(ctx, 3, components=random_components(random.Random(3)), engaged=False, sent_days_ago=5)
    lost.stage = OpportunityStage.LOST.value
    rows = loop.labelled_deals(ctx.session, ctx.now)
    assert len(rows) == 1 and rows[0][1] == 0
