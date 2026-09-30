"""Deterministic landed-cost and margin maths.

Every input is either supplied or explicitly unknown. Nothing is inferred by a
model here: if a required cost is missing, the calculation raises rather than
inventing a number.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.errors import UnknownInput
from app.core.types import Money

REQUIRED = ("selling_price", "acquisition_cost")


class DealInputs(BaseModel):
    quantity: int = Field(ge=1)
    selling_price: Money | None = None  # per unit
    acquisition_cost: Money | None = None  # per unit
    shipping: Money | None = None  # total
    insurance: Money | None = None  # total
    duties_taxes_pct: float | None = None  # on landed goods value
    transaction_cost_pct: float | None = None  # payment/FX/platform
    other_costs: Money | None = None
    currency: str = "USD"


class DealEconomics(BaseModel):
    quantity: int
    revenue: Money
    landed_cost: Money
    gross_profit: Money
    gross_margin_pct_low: float
    gross_margin_pct_high: float
    confidence: float
    assumptions: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    @property
    def is_range(self) -> bool:
        return self.gross_profit.is_range

    def summary(self) -> dict:
        return {
            "quantity": self.quantity,
            "revenue_usd": [self.revenue.low, self.revenue.high],
            "landed_cost_usd": [self.landed_cost.low, self.landed_cost.high],
            "gross_profit_usd": [self.gross_profit.low, self.gross_profit.high],
            "gross_margin_pct": [self.gross_margin_pct_low, self.gross_margin_pct_high],
            "confidence": self.confidence,
            "unknowns": self.unknowns,
            "assumptions": self.assumptions,
        }


def _zero(currency: str) -> Money:
    return Money(low=0.0, high=0.0, currency=currency, confidence=1.0, basis="not applicable")


def compute_economics(inputs: DealInputs) -> DealEconomics:
    missing = [name for name in REQUIRED if getattr(inputs, name) is None]
    if missing:
        raise UnknownInput(
            "cannot compute economics without required inputs", missing=missing
        )

    qty = inputs.quantity
    price = inputs.selling_price
    cost = inputs.acquisition_cost
    assert price is not None and cost is not None

    unknowns: list[str] = []
    assumptions: list[str] = []

    def optional(value: Money | None, label: str) -> Money:
        if value is None:
            unknowns.append(label)
            assumptions.append(f"{label} treated as zero and reported as unknown")
            return _zero(inputs.currency)
        return value

    shipping = optional(inputs.shipping, "shipping")
    insurance = optional(inputs.insurance, "insurance")
    other = optional(inputs.other_costs, "other_costs")

    revenue = Money(
        low=price.low * qty,
        high=price.high * qty,
        currency=inputs.currency,
        confidence=price.confidence,
        basis="selling_price x quantity",
    )
    goods = Money(
        low=cost.low * qty,
        high=cost.high * qty,
        currency=inputs.currency,
        confidence=cost.confidence,
        basis="acquisition_cost x quantity",
    )

    duties_pct = inputs.duties_taxes_pct
    if duties_pct is None:
        unknowns.append("duties_taxes_pct")
        assumptions.append("duties/taxes unknown: excluded from cost, margin is an upper bound")
        duties_pct = 0.0

    txn_pct = inputs.transaction_cost_pct
    if txn_pct is None:
        unknowns.append("transaction_cost_pct")
        assumptions.append("transaction costs unknown: excluded from cost")
        txn_pct = 0.0

    goods_plus_freight_low = goods.low + shipping.low + insurance.low + other.low
    goods_plus_freight_high = goods.high + shipping.high + insurance.high + other.high

    duties_low = goods_plus_freight_low * duties_pct
    duties_high = goods_plus_freight_high * duties_pct
    txn_low = revenue.low * txn_pct
    txn_high = revenue.high * txn_pct

    landed = Money(
        low=round(goods_plus_freight_low + duties_low + txn_low, 4),
        high=round(goods_plus_freight_high + duties_high + txn_high, 4),
        currency=inputs.currency,
        confidence=min(price.confidence, cost.confidence),
        basis="goods + freight + insurance + duties + transaction costs",
    )

    profit = Money(
        low=round(revenue.low - landed.high, 4),
        high=round(revenue.high - landed.low, 4),
        currency=inputs.currency,
        confidence=landed.confidence,
        basis="revenue - landed cost",
    )

    margin_low = round(profit.low / revenue.high * 100, 3) if revenue.high else 0.0
    margin_high = round(profit.high / revenue.low * 100, 3) if revenue.low else 0.0

    # Confidence decays with each unknown input.
    confidence = round(max(0.05, landed.confidence * (0.85 ** len(unknowns))), 4)

    return DealEconomics(
        quantity=qty,
        revenue=revenue,
        landed_cost=landed,
        gross_profit=profit,
        gross_margin_pct_low=margin_low,
        gross_margin_pct_high=margin_high,
        confidence=confidence,
        assumptions=assumptions,
        unknowns=unknowns,
    )
