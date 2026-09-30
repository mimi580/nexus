"""Real self-improvement: every decision NEXUS repeats is a learner fed by real outcomes.

What learns, from what, and where the result is used:

  e-mail angle and subject style   positive replies per first e-mail     SalesStrategyAgent → OutreachAgent
  outreach order                    positive replies per product×country  SalesStrategyAgent (task priority)
  market attractiveness             observed replies blended with research OpportunityScoringAgent, MarketResearch
  opportunity scoring weights       which scored deals actually replied   OpportunityScoringAgent (weekly refit)
  supplier regions                  regions whose suppliers sent quotes   SupplierResearchAgent
  ad angles, markets, budgets       enquiries per click and per dollar    app/ads (planner and optimiser)

Choices are made by Thompson sampling: each option is drawn from what the
data says about it, so proven options win most of the time while options
with little data still get tried. No threshold to tune, no option starved.

Changes to the scoring model are versioned Strategy rows. A new version is
only activated when it predicts better under cross-validation, each weight
moves at most 0.10 per refit, and it is rolled back automatically if it does
worse than its predecessor on the deals that arrive after it went live.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import OpportunityStage
from app.database.models import Company, Experiment, Interaction, Message, Opportunity, Strategy, SystemState
from app.learning import signals
from app.learning.bandits import RateArm, probability_best

EMAIL_ANGLES = {
    "cost": "unit cost versus buying new",
    "availability": "availability and lead time",
    "condition": "documented condition grading and warranty",
    "licensed": "licensed, documented supply into their country",
}
SUBJECT_STYLES = {
    "direct": "state the product and their organisation, e.g. '<product> supply for <organisation>'",
    "question": "a short question about their need, e.g. 'Sourcing <product> this quarter?'",
    "signal": "refer to the buying signal you saw, e.g. 'Re your <signal> - <product>'",
}
MIN_TRIALS_FOR_MARKET = 20
SCORING_STRATEGY = "scoring_weights"
MAX_WEIGHT_STEP = 0.10
MIN_LABELS = 30
MIN_PER_CLASS = 5
AUC_MARGIN = 0.02
ROLLBACK_MARGIN = 0.05
ROLLBACK_MIN_LABELS = 15


# ------------------------------------------------------------------ e-mail
def choose_email_variant(session: Session, category: str, seed: str, regulated: bool) -> dict[str, str]:
    angles = [a for a in EMAIL_ANGLES if a != "licensed" or regulated]
    rng = signals.rng_for("email", category, seed)
    angle_arms = signals.message_arms(session, category, "angle", angles)
    style_arms = signals.message_arms(session, category, "subject_style", list(SUBJECT_STYLES))
    angle = max(angle_arms, key=lambda a: a.sample(rng)).key
    style = max(style_arms, key=lambda a: a.sample(rng)).key
    return {"angle": angle, "subject_style": style}


def outreach_priority(session: Session, category: str, country: str | None, base: int, seed: str) -> int:
    """Segments that reply get contacted first; bounded to base-4 .. base+6."""
    arm = signals.segment_arms(session, [(category, country or "unknown")])[0]
    draw = arm.sample(signals.rng_for("priority", category, country, seed))
    return int(base - 4 + round(10 * min(1.0, draw / 0.10)))


def learned_market_score(session: Session, category: str, country: str | None, base: float) -> float:
    """Blend researched attractiveness with what outreach there actually returned (after 20 sends)."""
    if not country:
        return base
    arm = signals.segment_arms(session, [(category, country)])[0]
    if arm.trials < MIN_TRIALS_FOR_MARKET:
        return base
    observed = min(1.0, arm.mean / 0.10)  # a 10% positive-reply market scores 1.0
    weight = min(0.7, arm.trials / 100)  # evidence earns weight gradually, research never fully ignored
    return round((1 - weight) * base + weight * observed, 4)


# ------------------------------------------------------------------ suppliers
def supplier_region_arms(session: Session, category: str, regions: list[str]) -> list[RateArm]:
    found = {r: 0 for r in regions}
    quoted = {r: 0 for r in regions}
    for company in session.scalars(select(Company).where(Company.kind == "supplier")):
        profile = company.profile or {}
        region = profile.get("source_region") or company.country
        if region not in found or category not in (profile.get("categories") or []):
            continue
        found[region] += 1
        quoted[region] += int(company.status == "quoted")
    # Prior: about one supplier in five sends a quote.
    return [RateArm(r, quoted[r], found[r], 1.0, 4.0) for r in regions]


def rank_supplier_regions(session: Session, category: str, regions: list[str], k: int, seed: str) -> list[str]:
    if not regions:
        return []
    arms = supplier_region_arms(session, category, regions)
    rng = signals.rng_for("regions", category, seed)
    return [a.key for a in sorted(arms, key=lambda a: a.sample(rng), reverse=True)][:k]


# ------------------------------------------------------------------ scoring
def default_weights() -> dict[str, float]:
    from app.agents.qualification import SCORE_WEIGHTS

    return dict(SCORE_WEIGHTS)


def active_scoring(session: Session) -> Strategy | None:
    return session.scalar(select(Strategy).where(Strategy.name == SCORING_STRATEGY, Strategy.active.is_(True))
                          .order_by(Strategy.version.desc()))


def active_weights(session: Session) -> dict[str, float]:
    strategy = active_scoring(session)
    if strategy is not None and isinstance((strategy.parameters or {}).get("weights"), dict):
        return {k: float(v) for k, v in strategy.parameters["weights"].items()}
    return default_weights()


def score_with(weights: dict[str, float], components: dict[str, float]) -> float:
    total = 0.0
    for key, weight in weights.items():
        value = float(components.get(key, 0.0))
        total += value * weight if weight > 0 else (1.0 - value) * abs(weight)
    denominator = sum(abs(w) for w in weights.values()) or 1.0
    return max(0.0, min(1.0, total / denominator))


def labelled_deals(session: Session, now: datetime, since: datetime | None = None) -> list[tuple[dict[str, float], int, datetime]]:
    """(score components, 1 if the buyer engaged else 0, scored at) for contacted outbound deals.

    Positive: a positive reply, or the deal reached negotiation / won.
    Negative: contacted at least 21 days ago with no positive reply, or closed lost/stale after contact.
    Deals never contacted carry no label: the score decided they were not tried.
    """
    positive_opps = set(session.scalars(select(Interaction.opportunity_id).where(
        Interaction.direction == "inbound", Interaction.category.in_(signals.POSITIVE))))
    first_sent: dict[str, datetime] = {}
    for opp_id, sent_at in session.execute(select(Message.opportunity_id, Message.sent_at).where(
            Message.direction == "outbound", Message.status == "sent", Message.sequence_step == 0,
            Message.opportunity_id.is_not(None))):
        if sent_at is not None and (opp_id not in first_sent or sent_at < first_sent[opp_id]):
            first_sent[opp_id] = sent_at
    rows = []
    for opp in session.scalars(select(Opportunity).where(Opportunity.id.in_(list(first_sent)))):
        breakdown = opp.score_breakdown or {}
        components = breakdown.get("components")
        if not components or (opp.qualification or {}).get("inbound"):
            continue
        sent = first_sent[opp.id]
        naive_now = now.replace(tzinfo=None) if sent.tzinfo is None else now
        if since is not None and sent < (since.replace(tzinfo=None) if sent.tzinfo is None else since):
            continue
        engaged = opp.id in positive_opps or opp.stage in (OpportunityStage.NEGOTIATION.value, OpportunityStage.WON.value)
        if engaged:
            rows.append((components, 1, sent))
        elif opp.stage in (OpportunityStage.LOST.value, OpportunityStage.STALE.value) or naive_now - sent >= timedelta(days=21):
            rows.append((components, 0, sent))
    return rows


def auc(scores: list[float], labels: list[int]) -> float:
    """Probability a random engaged deal outranks a random non-engaged one (ties count half)."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return 0.5
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def fit_logistic(xs: list[list[float]], ys: list[int], l2: float = 1.0, steps: int = 400, lr: float = 0.5) -> tuple[list[float], float]:
    """L2-regularised logistic regression by gradient descent (small data, no dependencies)."""
    n, d = len(xs), len(xs[0])
    w, b = [0.0] * d, 0.0
    for _ in range(steps):
        gw, gb = [0.0] * d, 0.0
        for x, y in zip(xs, ys):
            z = b + sum(wi * xi for wi, xi in zip(w, x))
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
            err = p - y
            gb += err
            for j in range(d):
                gw[j] += err * x[j]
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j] / n)
        b -= lr * gb / n
    return w, b


def cross_validated_auc(xs: list[list[float]], ys: list[int], folds: int = 5, seed: int = 7) -> float:
    idx = list(range(len(xs)))
    random.Random(seed).shuffle(idx)
    scores, labels = [], []
    for f in range(folds):
        test = set(idx[f::folds])
        train = [i for i in idx if i not in test]
        w, b = fit_logistic([xs[i] for i in train], [ys[i] for i in train])
        for i in test:
            scores.append(b + sum(wj * xj for wj, xj in zip(w, xs[i])))
            labels.append(ys[i])
    return auc(scores, labels)


def propose_weights(current: dict[str, float], coefficients: list[float], keys: list[str]) -> dict[str, float]:
    """Logistic coefficients → scorer weights, each moved at most MAX_WEIGHT_STEP toward the fit.

    The target is the coefficients rescaled to the current weights' total size;
    the scorer divides by the sum of |weights|, so only proportions matter.
    """
    scale = sum(abs(v) for v in current.values()) or 1.0
    total = sum(abs(c) for c in coefficients) or 1.0
    target = {k: c / total * scale for k, c in zip(keys, coefficients)}
    new = {}
    for k in keys:
        step = max(-MAX_WEIGHT_STEP, min(MAX_WEIGHT_STEP, target[k] - current.get(k, 0.0)))
        new[k] = round(current.get(k, 0.0) + step, 4)
    return new


def calibrate_scoring(ctx: Any) -> dict[str, Any]:
    """Weekly refit. Activates a new weight version only if it ranks deals better out of sample."""
    session: Session = ctx.session
    rows = labelled_deals(session, ctx.now)
    labels = [y for _, y, _ in rows]
    report: dict[str, Any] = {"labels": len(rows), "positives": sum(labels)}
    if len(rows) < MIN_LABELS or sum(labels) < MIN_PER_CLASS or len(rows) - sum(labels) < MIN_PER_CLASS:
        report["decision"] = f"waiting for data: need {MIN_LABELS}+ contacted deals with {MIN_PER_CLASS}+ of each outcome"
        return report
    current = active_weights(session)
    keys = list(current)
    xs = [[float(c.get(k, 0.0)) for k in keys] for c, _, _ in rows]
    baseline = auc([score_with(current, c) for c, _, _ in rows], labels)
    cv = cross_validated_auc(xs, labels)
    report.update(baseline_auc=round(baseline, 4), model_cv_auc=round(cv, 4))
    if cv < baseline + AUC_MARGIN:
        report["decision"] = "kept current weights: the refit does not rank deals better out of sample"
        return report
    coefficients, _ = fit_logistic(xs, labels)
    proposed = propose_weights(current, coefficients, keys)
    proposed_auc = auc([score_with(proposed, c) for c, _, _ in rows], labels)
    report.update(proposed_weights=proposed, proposed_in_sample_auc=round(proposed_auc, 4))
    if proposed_auc <= baseline:
        report["decision"] = "kept current weights: the bounded step does not improve ranking"
        return report
    previous = active_scoring(session)
    version = (session.scalar(select(Strategy.version).where(Strategy.name == SCORING_STRATEGY)
                              .order_by(Strategy.version.desc())) or 0) + 1
    if previous is not None:
        previous.active = False
        previous.retired_at = ctx.now
    strategy = Strategy(name=SCORING_STRATEGY, version=version, active=True, activated_at=ctx.now,
                        parameters={"weights": proposed, "previous_weights": current, "baseline_auc": round(baseline, 4),
                                    "cv_auc": round(cv, 4), "labels": len(rows)},
                        rationale=f"refit on {len(rows)} contacted deals: cross-validated AUC {cv:.3f} vs {baseline:.3f}")
    session.add(strategy)
    session.add(Experiment(strategy_name=SCORING_STRATEGY, control_version=previous.version if previous else 0,
                           variant_version=version, status="running", metric="auc",
                           result={"baseline_auc": baseline, "cv_auc": cv, "activated_at": ctx.now.isoformat()}))
    session.flush()
    report["decision"] = f"activated scoring weights v{version}"
    return report


def check_rollback(ctx: Any) -> dict[str, Any]:
    """Roll the active scoring version back if it ranks new deals worse than the one it replaced."""
    session: Session = ctx.session
    active = active_scoring(session)
    if active is None or active.activated_at is None:
        return {"rollback": "nothing to check"}
    rows = labelled_deals(session, ctx.now, since=active.activated_at)
    if len(rows) < ROLLBACK_MIN_LABELS:
        return {"rollback": f"{len(rows)} new labelled deals since v{active.version}; need {ROLLBACK_MIN_LABELS}"}
    labels = [y for _, y, _ in rows]
    new_auc = auc([score_with(active.parameters["weights"], c) for c, _, _ in rows], labels)
    old_weights = active.parameters.get("previous_weights") or default_weights()
    old_auc = auc([score_with(old_weights, c) for c, _, _ in rows], labels)
    experiment = session.scalar(select(Experiment).where(Experiment.strategy_name == SCORING_STRATEGY,
                                                         Experiment.variant_version == active.version))
    if new_auc + ROLLBACK_MARGIN < old_auc:
        rollback_scoring(ctx, reason=f"v{active.version} AUC {new_auc:.3f} vs previous {old_auc:.3f} on {len(rows)} new deals")
        if experiment:
            experiment.status, experiment.decision = "concluded", "rolled_back"
        return {"rollback": "rolled back", "new_auc": new_auc, "old_auc": old_auc}
    if experiment and len(rows) >= 2 * ROLLBACK_MIN_LABELS:
        experiment.status, experiment.decision = "concluded", "kept"
        experiment.result = {**(experiment.result or {}), "live_auc": new_auc, "previous_live_auc": old_auc}
    return {"rollback": "kept", "new_auc": round(new_auc, 4), "old_auc": round(old_auc, 4)}


def rollback_scoring(ctx: Any, reason: str, actor: str = "nexus") -> Strategy | None:
    """Deactivate the active version; the previous version (or the defaults) takes over."""
    session: Session = ctx.session
    active = active_scoring(session)
    if active is None:
        return None
    active.active = False
    active.retired_at = ctx.now
    previous = session.scalar(select(Strategy).where(Strategy.name == SCORING_STRATEGY, Strategy.version < active.version,
                                                     Strategy.retired_at.is_not(None)).order_by(Strategy.version.desc()))
    if previous is not None:
        previous.active = True
        previous.retired_at = None
    ctx.audit.record("strategy_rolled_back", summary=f"{SCORING_STRATEGY} v{active.version}: {reason}", actor=actor,
                     decision="allow", restored=previous.version if previous else "defaults")
    session.flush()
    return previous


# ------------------------------------------------------------------ snapshot
def snapshot(ctx: Any) -> dict[str, Any]:
    """What NEXUS currently believes, for the dashboard's Learning tab."""
    from app.commercial import settings as commercial
    from app.core.types import ProductCategory, REGULATED_CATEGORIES

    session: Session = ctx.session
    rng = signals.rng_for("snapshot", ctx.now.date())
    regulated = {c.value for c in REGULATED_CATEGORIES}
    out: dict[str, Any] = {"generated_at": ctx.now.isoformat(), "email": {}, "ads": {}, "suppliers": {}, "scoring": {}}

    def describe(arms: list[RateArm]) -> list[dict[str, Any]]:
        pb = probability_best(arms, rng, 1000) if arms else {}
        return [{"option": a.key, "successes": a.successes, "trials": a.trials, "rate": round(a.mean, 4),
                 "interval": [round(x, 4) for x in a.interval()], "p_best": round(pb.get(a.key, 0.0), 3)} for a in arms]

    settings = commercial.get(session)
    for category in [c.value for c in ProductCategory]:
        angles = [a for a in EMAIL_ANGLES if a != "licensed" or category in regulated]
        out["email"][category] = {
            "angle": describe(signals.message_arms(session, category, "angle", angles)),
            "subject_style": describe(signals.message_arms(session, category, "subject_style", list(SUBJECT_STYLES))),
        }
        from app.ads.compliance import ad_allowed
        from app.ads.copy import ANGLES

        if ad_allowed(category):
            out["ads"][category] = {p: describe(signals.ad_angle_arms(session, p, category, ANGLES)) for p in ("google", "meta")}
        out["suppliers"][category] = describe(supplier_region_arms(session, category, settings["supplier_regions"].get(category, [])))
    strategy = active_scoring(session)
    out["scoring"] = {
        "active_version": strategy.version if strategy else 0,
        "weights": active_weights(session),
        "history": [{"version": s.version, "active": s.active, "activated_at": s.activated_at.isoformat() if s.activated_at else None,
                     "retired_at": s.retired_at.isoformat() if s.retired_at else None, "rationale": s.rationale}
                    for s in session.scalars(select(Strategy).where(Strategy.name == SCORING_STRATEGY).order_by(Strategy.version.desc()))],
    }
    return out


def store_snapshot(ctx: Any, data: dict[str, Any]) -> None:
    row = ctx.session.get(SystemState, "learning_snapshot")
    if row is None:
        ctx.session.add(SystemState(key="learning_snapshot", value=data))
    else:
        row.value = data
    ctx.session.flush()
