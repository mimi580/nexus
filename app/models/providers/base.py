from __future__ import annotations

import json
import re
from typing import Any

from app.core.errors import MalformedModelOutput
from app.core.interfaces import ModelRequest, ModelResponse
from app.core.types import ModelTier

# USD per 1M tokens, per logical tier. Used for estimation and for the ledger.
TIER_RATES: dict[ModelTier, tuple[float, float]] = {
    ModelTier.REASONING: (3.0, 15.0),
    ModelTier.BULK: (0.25, 1.25),
    ModelTier.CRITIC: (0.80, 4.00),
}

FENCE_RE = re.compile(r"```(?:json)?(.*?)```", re.DOTALL)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def render_user_message(request: ModelRequest) -> str:
    """The instruction plus the structured context the model must work from.

    Live providers send this as the user turn, so the model sees exactly the
    facts the agent supplied and nothing it was not given.
    """
    if not request.context:
        return request.prompt
    context = json.dumps(request.context, default=str, ensure_ascii=False, indent=1)
    return f"{request.prompt}\n\nContext (JSON; the only facts you may use):\n{context}"


def price(tier: ModelTier, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = TIER_RATES[tier]
    return round((input_tokens * rate_in + output_tokens * rate_out) / 1_000_000, 8)


def parse_json(response: ModelResponse | str) -> dict[str, Any]:
    """Parse a model's JSON payload, tolerating fences. Never guesses content."""
    text = response if isinstance(response, str) else response.text
    candidate = text.strip()
    fenced = FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1:
        raise MalformedModelOutput("no JSON object in model output", preview=candidate[:200])
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise MalformedModelOutput("model output is not valid JSON", error=str(exc)) from exc
    if not isinstance(data, dict):
        raise MalformedModelOutput("model output is not a JSON object")
    return data


class BaseProvider:
    name = "base"
    supported_tiers: tuple[ModelTier, ...] = tuple(ModelTier)

    def supports(self, tier: ModelTier) -> bool:
        return tier in self.supported_tiers

    def estimate_cost(self, request: ModelRequest) -> float:
        in_tokens = estimate_tokens(render_user_message(request)) + estimate_tokens(request.system or "")
        return price(request.tier, in_tokens, request.max_output_tokens)

    def complete(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover - abstract
        raise NotImplementedError
