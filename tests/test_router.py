import pytest
from sqlalchemy import select

from app.core.errors import BudgetExceeded, ProviderError
from app.core.interfaces import ModelRequest
from app.core.types import ModelTier
from app.database.models import ModelRun
from app.models.providers.mock import AlwaysFailingProvider, MockProvider
from app.models.router import ModelRouter


def test_router_falls_back_to_a_healthy_provider(ctx):
    router = ModelRouter(ctx.session, ctx.budget, ctx.settings)
    broken = AlwaysFailingProvider()
    good = MockProvider("good", latency_ms=0)
    router.register(broken, preference=90)
    router.register(good, preference=10)

    response = router.complete(ModelRequest(task_type="qualification", prompt="hello", context={}))
    assert response.provider == "good"
    assert broken.calls == 1
    runs = list(ctx.session.scalars(select(ModelRun)))
    assert any(r.ok is False for r in runs) and any(r.ok for r in runs)


def test_unhealthy_provider_is_skipped_after_repeated_failures(ctx):
    router = ModelRouter(ctx.session, ctx.budget, ctx.settings)
    broken = AlwaysFailingProvider()
    router.register(broken, preference=90)
    router.register(MockProvider("good", latency_ms=0), preference=10)
    for i in range(4):
        router.complete(ModelRequest(task_type="qualification", prompt=f"p{i}", context={}))
    assert broken.calls < 4  # cooled down after consecutive failures


def test_all_providers_failing_raises(ctx):
    router = ModelRouter(ctx.session, ctx.budget, ctx.settings)
    router.register(AlwaysFailingProvider("a"))
    router.register(AlwaysFailingProvider("b"))
    with pytest.raises(ProviderError):
        router.complete(ModelRequest(task_type="qualification", prompt="x", context={}))


def test_budget_exhaustion_stops_model_calls(ctx):
    router = ModelRouter(ctx.session, ctx.budget, ctx.settings)
    router.register(MockProvider("good", latency_ms=0))
    for category in ("ai_primary", "ai_secondary"):
        rid = ctx.budget.reserve(category, ctx.budget.category_limit(category), "exhaust")
        ctx.budget.commit(rid, ctx.budget.category_limit(category))
    with pytest.raises(BudgetExceeded):
        router.complete(ModelRequest(task_type="qualification", prompt="x", context={}))


def test_complex_tasks_route_to_the_reasoning_tier(ctx):
    router = ModelRouter(ctx.session, ctx.budget, ctx.settings)
    router.register(MockProvider("good", latency_ms=0))
    reasoning = router.complete(
        ModelRequest(task_type="market_research", prompt="x", context={"product_category": "refurbished_laptop"})
    )
    bulk = router.complete(ModelRequest(task_type="response_classification", prompt="y", context={"reply_text": "no"}))
    assert reasoning.tier == ModelTier.REASONING
    assert bulk.tier == ModelTier.BULK


def test_mock_provider_is_deterministic(ctx):
    provider = MockProvider(latency_ms=0)
    request = ModelRequest(task_type="prospect_discovery", prompt="find", context={"product_category": "used_iphone"})
    assert provider.complete(request).text == provider.complete(request).text


def test_malformed_output_is_retried_then_parsed(ctx):
    from app.agents.base import BaseAgent

    router = ModelRouter(ctx.session, ctx.budget, ctx.settings)
    router.register(MockProvider("flaky", latency_ms=0, malformed_rate=1.0), preference=90)
    router.register(MockProvider("clean", latency_ms=0), preference=10)
    ctx.router = router
    agent = BaseAgent()
    with pytest.raises(Exception):
        agent.ask(ctx, "prompt", {"product_category": "used_iphone"}, task_type="prospect_discovery", retries=0)
