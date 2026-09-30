"""Provider-neutral outbound communication layer.

Simulation is the default. A production adapter implements the same protocol;
policy, budget, dedupe and rate limits are enforced before send() is ever
called, in app.agents.outreach.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings
from app.core.errors import ProviderUnavailable
from app.core.ids import new_id, stable_key
from app.simulation import fixtures


@dataclass
class SendResult:
    ok: bool
    provider: str
    provider_message_id: str | None = None
    simulated: bool = True
    error: str | None = None
    simulated_reply: str | None = None
    simulated_reply_category: str | None = None
    reply_delay_hours: float | None = None
    cost_usd: float = 0.0


class EmailProvider(Protocol):
    name: str

    def send(self, *, to: str, subject: str, body: str, context: dict) -> SendResult: ...


class SimulatedEmailProvider:
    """Sends nothing. Produces a deterministic, seeded reply for the loop to process."""

    name = "simulated"

    def __init__(self, seed: int = 20260917, failure_rate: float = 0.0) -> None:
        self.seed = seed
        self.failure_rate = failure_rate
        self.sent: list[dict] = []

    def send(self, *, to: str, subject: str, body: str, context: dict) -> SendResult:
        rng = random.Random(int(stable_key(to, subject, str(self.seed))[:8], 16))
        if self.failure_rate and rng.random() < self.failure_rate:
            return SendResult(ok=False, provider=self.name, error="simulated transport failure")

        self.sent.append({"to": to, "subject": subject, "body": body})
        category = (context or {}).get("product_category")
        mix = fixtures.RESPONSE_MIX.get(category) or fixtures.RESPONSE_MIX[
            fixtures.ProductCategory.LAPTOP.value
        ]
        labels = [label for label, _ in mix]
        weights = [weight for _, weight in mix]
        outcome = rng.choices(labels, weights=weights, k=1)[0]
        reply = None if outcome == "no_reply" else fixtures.REPLY_TEMPLATES.get(outcome)
        return SendResult(
            ok=True,
            provider=self.name,
            provider_message_id=new_id("sim"),
            simulated=True,
            simulated_reply=reply,
            simulated_reply_category=None if reply is None else outcome,
            reply_delay_hours=round(rng.uniform(2, 48), 1) if reply else None,
            cost_usd=0.0,
        )


class SmtpEmailProvider:
    """Production adapter skeleton. Credentials come from settings only."""

    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        if not (settings.email_sender_address and settings.email_api_key):
            raise ProviderUnavailable("email credentials are not configured")
        self.settings = settings

    def send(self, *, to: str, subject: str, body: str, context: dict) -> SendResult:  # pragma: no cover
        raise NotImplementedError(
            "Wire an SMTP/API client here. Simulation must pass before enabling this adapter."
        )


def build_email_provider(settings: Settings) -> EmailProvider:
    if settings.nexus_mode == "simulation" or settings.email_provider == "simulated":
        return SimulatedEmailProvider(seed=settings.random_seed)
    return SmtpEmailProvider(settings)
