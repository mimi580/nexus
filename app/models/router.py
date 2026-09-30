"""Model router: tiers, health, fallback, cost accounting.

Every model call is budgeted before it happens and recorded after it happens.
No agent talks to a provider directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.budget.controller import BudgetController
from app.core.config import Settings, get_settings
from app.core.errors import BudgetExceeded, ProviderError
from app.core.interfaces import ModelProvider, ModelRequest, ModelResponse
from app.core.logging import get_logger, log_event
from app.core.types import ModelTier
from app.database.models import ModelRun

logger = get_logger("nexus.router")

TIER_COST_CATEGORY = {
    ModelTier.REASONING: "ai_primary",
    ModelTier.BULK: "ai_secondary",
    ModelTier.CRITIC: "ai_secondary",
}

COMPLEX_TASKS = {"market_research", "sales_strategy", "learning_review"}
BULK_TASKS = {"prospect_discovery", "company_intelligence", "decision_maker_discovery", "response_classification"}


@dataclass
class ProviderHealth:
    consecutive_failures: int = 0
    total_calls: int = 0
    total_failures: int = 0
    total_latency_ms: float = 0.0
    disabled_until_call: int = 0

    @property
    def failure_rate(self) -> float:
        return self.total_failures / self.total_calls if self.total_calls else 0.0


@dataclass
class Registration:
    provider: ModelProvider
    tiers: tuple[ModelTier, ...]
    preference: int = 50
    health: ProviderHealth = field(default_factory=ProviderHealth)


class ModelRouter:
    """Routes by task type, complexity, provider health and historical latency."""

    def __init__(
        self,
        session: Session,
        budget: BudgetController,
        settings: Settings | None = None,
        cooldown_calls: int = 5,
    ) -> None:
        self.session = session
        self.budget = budget
        self.settings = settings or get_settings()
        self.registrations: list[Registration] = []
        self.cooldown_calls = cooldown_calls
        self._call_counter = 0

    def register(self, provider: ModelProvider, tiers: tuple[ModelTier, ...] | None = None, preference: int = 50) -> None:
        resolved = tiers or tuple(t for t in ModelTier if provider.supports(t))
        self.registrations.append(Registration(provider=provider, tiers=resolved, preference=preference))

    # ------------------------------------------------------------- routing
    def choose_tier(self, request: ModelRequest) -> ModelTier:
        if request.tier != ModelTier.BULK:  # explicit caller choice wins
            return request.tier
        if request.task_type in COMPLEX_TASKS or request.complexity >= 0.7:
            return ModelTier.REASONING
        if request.task_type in BULK_TASKS or request.complexity <= 0.4:
            return ModelTier.BULK
        return ModelTier.REASONING if request.complexity > 0.55 else ModelTier.BULK

    def candidates(self, tier: ModelTier) -> list[Registration]:
        available = [
            r
            for r in self.registrations
            if tier in r.tiers and r.health.disabled_until_call <= self._call_counter
        ]
        if not available:  # every provider is cooling down: try them all anyway
            available = [r for r in self.registrations if tier in r.tiers]
        return sorted(
            available,
            key=lambda r: (-r.preference, r.health.failure_rate, r.health.total_latency_ms / max(r.health.total_calls, 1)),
        )

    # ------------------------------------------------------------ execution
    def complete(self, request: ModelRequest) -> ModelResponse:
        tier = self.choose_tier(request)
        routed = request.model_copy(update={"tier": tier})
        category = TIER_COST_CATEGORY[tier]
        candidates = self.candidates(tier)
        if not candidates:
            raise ProviderError("no provider registered for tier", tier=tier.value)

        last_error: Exception | None = None
        for registration in candidates:
            provider = registration.provider
            estimate = max(provider.estimate_cost(routed), 1e-6)
            try:
                reservation = self.budget.reserve(
                    category, estimate, f"model:{provider.name}:{request.task_type}"
                )
            except BudgetExceeded:
                self._record_run(routed, provider.name, "n/a", None, ok=False, error="budget_exceeded")
                raise

            self._call_counter += 1
            registration.health.total_calls += 1
            try:
                response = provider.complete(routed)
            except Exception as exc:  # provider failure -> release funds, try next
                self.budget.release(reservation)
                registration.health.total_failures += 1
                registration.health.consecutive_failures += 1
                if registration.health.consecutive_failures >= 2:
                    registration.health.disabled_until_call = self._call_counter + self.cooldown_calls
                self._record_run(routed, provider.name, "n/a", None, ok=False, error=str(exc))
                log_event(
                    logger,
                    logging.WARNING,
                    "provider_failed",
                    provider=provider.name,
                    task_type=request.task_type,
                    error=str(exc),
                )
                last_error = exc
                continue

            self.budget.commit(reservation, response.cost_usd)
            registration.health.consecutive_failures = 0
            registration.health.total_latency_ms += response.latency_ms
            self._record_run(routed, provider.name, response.model, response, ok=True)
            return response

        raise ProviderError(
            "all providers failed for tier",
            tier=tier.value,
            task_type=request.task_type,
            last_error=str(last_error) if last_error else None,
        )

    def _record_run(
        self,
        request: ModelRequest,
        provider: str,
        model: str,
        response: ModelResponse | None,
        *,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self.session.add(
            ModelRun(
                task_type=request.task_type,
                tier=request.tier.value,
                provider=provider,
                model=model,
                input_tokens=response.input_tokens if response else 0,
                output_tokens=response.output_tokens if response else 0,
                cost_usd=response.cost_usd if response else 0.0,
                latency_ms=response.latency_ms if response else 0.0,
                ok=ok,
                error=error,
            )
        )
        self.session.flush()


def build_default_router(session: Session, budget: BudgetController, settings: Settings | None = None) -> ModelRouter:
    """Mock providers unless real credentials exist and mode is production."""
    from app.models.providers.mock import MockProvider

    settings = settings or get_settings()
    router = ModelRouter(session, budget, settings)
    if settings.nexus_mode == "production" and settings.anthropic_api_key:
        from app.models.providers.anthropic_provider import AnthropicProvider

        router.register(AnthropicProvider(settings), preference=90)
    router.register(MockProvider("mock-primary"), preference=50)
    router.register(MockProvider("mock-secondary", latency_ms=2.0), preference=40)
    return router
