"""Small, dependency-free Bayesian tools for deciding under uncertainty.

- Beta-Bernoulli arms for rates (reply rate, lead-per-click rate).
- Gamma-Poisson arms for counts per unit of cost (leads per dollar).
- Thompson sampling to choose, probability-of-best to prune.

Everything is seeded from a caller-supplied random.Random so decisions are
reproducible in tests and simulation.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RateArm:
    """Successes out of trials, with a Beta(prior_a, prior_b) prior."""

    key: str
    successes: float
    trials: float
    prior_a: float = 1.0
    prior_b: float = 1.0

    @property
    def alpha(self) -> float:
        return self.prior_a + max(self.successes, 0.0)

    @property
    def beta(self) -> float:
        return self.prior_b + max(self.trials - self.successes, 0.0)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def interval(self, z: float = 1.64) -> tuple[float, float]:
        a, b = self.alpha, self.beta
        var = a * b / ((a + b) ** 2 * (a + b + 1))
        sd = math.sqrt(var)
        return max(0.0, self.mean - z * sd), min(1.0, self.mean + z * sd)

    def sample(self, rng: random.Random) -> float:
        return rng.betavariate(self.alpha, self.beta)


@dataclass(frozen=True)
class CountArm:
    """Events per unit exposure (e.g. leads per dollar), Gamma(shape, rate) prior."""

    key: str
    events: float
    exposure: float
    prior_shape: float = 1.0
    prior_rate: float = 1.0

    @property
    def shape(self) -> float:
        return self.prior_shape + max(self.events, 0.0)

    @property
    def rate(self) -> float:
        return self.prior_rate + max(self.exposure, 0.0)

    @property
    def mean(self) -> float:
        return self.shape / self.rate

    def sample(self, rng: random.Random) -> float:
        return rng.gammavariate(self.shape, 1.0 / self.rate)


def thompson_choice(arms: list, rng: random.Random) -> str:
    """The key of the arm with the highest posterior draw."""
    if not arms:
        raise ValueError("no arms to choose from")
    return max(arms, key=lambda arm: arm.sample(rng)).key


def probability_best(arms: list, rng: random.Random, draws: int = 2000) -> dict[str, float]:
    """Monte Carlo estimate of P(arm is best) for each arm."""
    if not arms:
        return {}
    wins = {arm.key: 0 for arm in arms}
    for _ in range(draws):
        best = max(arms, key=lambda arm: arm.sample(rng))
        wins[best.key] += 1
    return {key: count / draws for key, count in wins.items()}


def thompson_allocation(arms: list, total: float, rng: random.Random, draws: int = 500) -> dict[str, float]:
    """Split a total (e.g. daily budget) in proportion to P(best) — probability matching."""
    if not arms:
        return {}
    share = probability_best(arms, rng, draws)
    return {key: total * p for key, p in share.items()}
