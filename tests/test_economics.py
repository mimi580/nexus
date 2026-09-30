import pytest

from app.core.errors import UnknownInput
from app.core.types import Money
from app.economics.calculator import DealInputs, compute_economics


def test_missing_required_input_raises_instead_of_guessing():
    with pytest.raises(UnknownInput):
        compute_economics(DealInputs(quantity=10, selling_price=Money.exact(100)))


def test_margin_math_with_all_inputs():
    result = compute_economics(
        DealInputs(
            quantity=10,
            selling_price=Money.exact(200),
            acquisition_cost=Money.exact(120),
            shipping=Money.exact(100),
            insurance=Money.exact(0),
            other_costs=Money.exact(0),
            duties_taxes_pct=0.1,
            transaction_cost_pct=0.02,
        )
    )
    # goods 1200 + freight 100 = 1300; duties 130; txn 40 -> landed 1470; revenue 2000
    assert result.landed_cost.low == pytest.approx(1470.0)
    assert result.gross_profit.low == pytest.approx(530.0)
    assert result.gross_margin_pct_low == pytest.approx(26.5, rel=1e-3)
    assert result.unknowns == []


def test_unknown_inputs_are_reported_and_reduce_confidence():
    result = compute_economics(
        DealInputs(
            quantity=5,
            selling_price=Money(low=90, high=110),
            acquisition_cost=Money(low=60, high=70),
        )
    )
    assert "shipping" in result.unknowns
    assert "duties_taxes_pct" in result.unknowns
    assert result.confidence < 0.5
    assert result.is_range
