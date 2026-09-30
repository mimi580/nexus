import pytest

from app.budget.controller import BudgetController
from app.core.config import HARD_MONTHLY_CEILING_USD, Settings
from app.core.errors import BudgetExceeded


def test_monthly_limit_defaults_to_500_and_is_operator_set():
    assert Settings(_env_file=None).budget_monthly_limit_usd == HARD_MONTHLY_CEILING_USD == 500.0
    assert Settings(_env_file=None, budget_monthly_limit_usd=800).budget_monthly_limit_usd == 800.0
    with pytest.raises(ValueError):
        Settings(_env_file=None, budget_monthly_limit_usd=0)


def test_raised_ceiling_applies_mid_month_and_lifts_the_hard_stop(session, settings):
    budget = BudgetController(session, settings)
    for category, limit in settings.category_limits.items():
        budget.record_direct(category, limit, "exhaust")
    assert budget.hard_stopped()
    budget.settings = settings.model_copy(update={
        "budget_monthly_limit_usd": 700.0,
        "budget_category_limits_json": '{"ads": 500, "email": 25}',
    })
    assert budget.limit() == 700.0
    assert budget.hard_stopped() is False
    assert budget.can_spend("ads", 150.0)


def test_externally_incurred_spend_is_always_recorded_and_stops_work(session, settings):
    budget = BudgetController(session, settings)
    budget.record_incurred("ads", 480.0, "platform spend")
    budget.record_incurred("ads", 40.0, "late-reported platform spend")
    assert budget.committed(category="ads") == 520.0
    assert budget.hard_stopped() is True


def test_reservation_beyond_ceiling_is_rejected(session, settings):
    budget = BudgetController(session, settings)
    budget.record_direct("reserve", 30.0, "infra")
    with pytest.raises(BudgetExceeded):
        budget.reserve("reserve", 5.0, "too much for the category")
    assert budget.remaining() == pytest.approx(470.0)


def test_hard_ceiling_holds_across_many_small_spends(session, settings):
    budget = BudgetController(session, settings)
    spent = 0.0
    rejected = 0
    for _ in range(300):
        try:
            rid = budget.reserve("ai_primary", 1.0, "model call")
            spent += budget.commit(rid, 1.0)
        except BudgetExceeded:
            rejected += 1
    assert rejected > 0
    assert spent <= budget.category_limit("ai_primary")
    assert budget.encumbered() <= HARD_MONTHLY_CEILING_USD


def test_commit_overrun_is_clamped_not_allowed(session, settings):
    budget = BudgetController(session, settings)
    budget.record_direct("email", 24.5, "bulk sends")
    rid = budget.reserve("email", 0.5, "one more send")
    actual = budget.commit(rid, 100.0)
    assert actual <= 25.0
    assert budget.encumbered(category="email") <= budget.category_limit("email")


def test_release_returns_funds(session, settings):
    budget = BudgetController(session, settings)
    rid = budget.reserve("ai_primary", 10.0, "planned call")
    assert budget.remaining() == 490.0
    budget.release(rid)
    assert budget.remaining() == 500.0


def test_hard_stop_engages_when_exhausted(session, settings):
    budget = BudgetController(session, settings)
    for category, limit in settings.category_limits.items():
        rid = budget.reserve(category, limit, "exhaust")
        budget.commit(rid, limit)
    assert budget.hard_stopped() is True
    assert budget.can_spend("ai_primary", 0.01) is False
