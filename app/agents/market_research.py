"""Market research + geographic intelligence.

Country attractiveness is recomputed from evidence every cycle and stored with
an expiry. Nothing is hard-coded as 'the best country'.
"""

from __future__ import annotations

from datetime import timedelta

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.interfaces import AgentResult
from app.core.types import EvidenceKind, ModelTier
from app.database.models import MarketAssessment

# Higher is better; competition, regulation burden and payment risk are inverted.
DEFAULT_WEIGHTS = {
    "demand": 0.26,
    "procurement": 0.16,
    "purchasing_power": 0.12,
    "supplier_availability": 0.14,
    "logistics": 0.10,
    "competition": -0.10,
    "payment_risk": -0.14,
    "regulation": 0.08,
}

ASSESSMENT_TTL_DAYS = 14


def score_market(factors: dict[str, float], weights: dict[str, float] | None = None) -> float:
    w = weights or DEFAULT_WEIGHTS
    total = 0.0
    for key, weight in w.items():
        value = float(factors.get(key, 0.5))
        total += value * weight if weight > 0 else (1.0 - value) * abs(weight)
    denominator = sum(abs(x) for x in w.values()) or 1.0
    return round(max(0.0, min(1.0, total / denominator)), 4)


class MarketResearchAgent(BaseAgent):
    name = "market_research"
    task_type = "market_research"
    tier = ModelTier.REASONING
    complexity = 0.8

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        category = task_input["product_category"]
        weights = task_input.get("weights") or DEFAULT_WEIGHTS
        top_n = int(task_input.get("top_n", 3))

        prompt = (
            f"Assess country-level commercial attractiveness for {category}. "
            "Return {'markets':[{'country','region','factors':{...},'rationale','sources':[]}]}."
        )
        data, cost = self.ask(ctx, prompt, {"product_category": category})
        markets = data.get("markets") or []
        if not markets:
            return self.fail("no markets returned", cost_usd=cost)

        scored = []
        evidence = []
        for market in markets:
            factors = market.get("factors") or {}
            score = score_market(factors, weights)
            ctx.session.add(
                MarketAssessment(
                    country=market["country"],
                    region=market.get("region", ""),
                    product_category=category,
                    score=score,
                    factors=factors,
                    rationale=market.get("rationale", ""),
                    expires_at=ctx.now + timedelta(days=ASSESSMENT_TTL_DAYS),
                )
            )
            scored.append({"country": market["country"], "score": score, "region": market.get("region", "")})
            evidence.append(
                self.evidence(
                    f"{market['country']} attractiveness for {category}: {score}",
                    source=(market.get("sources") or ["model"])[0],
                    kind=EvidenceKind.INFERENCE,
                    confidence=0.45,
                    subject_type="market",
                    subject_id=market["country"],
                )
            )
        ctx.session.flush()
        self.persist_evidence(ctx, evidence)

        scored.sort(key=lambda m: m["score"], reverse=True)
        top = scored[:top_n]
        return self.ok(
            output={"ranked_markets": scored, "selected": top},
            cost_usd=cost,
            evidence=evidence,
            next_tasks=[
                {
                    "agent": "prospect_discovery",
                    "input": {
                        "product_category": category,
                        "countries": [m["country"] for m in top],
                        "limit": int(task_input.get("prospect_limit", 6)),
                    },
                    "priority": 70,
                }
            ],
            notes=[f"top markets: {', '.join(m['country'] for m in top)}"],
        )
