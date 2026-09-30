"""Production adapter. Credentials come only from settings/environment.

Kept deliberately thin: business logic never imports this module directly, it
only ever sees the ModelProvider protocol.
"""

from __future__ import annotations

import time

import httpx

from app.core.config import Settings
from app.core.errors import ProviderError, ProviderUnavailable
from app.core.interfaces import ModelRequest, ModelResponse
from app.core.types import ModelTier
from app.models.providers.base import BaseProvider, price, render_user_message

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, settings: Settings, models: dict[ModelTier, str] | None = None, timeout: float = 60.0) -> None:
        if not settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not configured")
        self._api_key = settings.anthropic_api_key
        self.models = models or {
            ModelTier.REASONING: settings.model_primary or "claude-sonnet-5-5",
            ModelTier.BULK: settings.model_bulk or "claude-haiku-4-5-20251001",
            ModelTier.CRITIC: settings.model_critic or "claude-haiku-4-5-20251001",
        }
        self.timeout = timeout

    def complete(self, request: ModelRequest) -> ModelResponse:
        model = self.models[request.tier]
        payload = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "messages": [{"role": "user", "content": render_user_message(request)}],
        }
        if request.system:
            payload["system"] = request.system
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        started = time.perf_counter()
        try:
            response = httpx.post(API_URL, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:  # network failure -> fallback-able
            raise ProviderUnavailable("anthropic request failed", error=str(exc)) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable("anthropic unavailable", status=response.status_code)
        if response.status_code >= 400:
            raise ProviderError("anthropic rejected the request", status=response.status_code)

        data = response.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage", {})
        in_tokens = int(usage.get("input_tokens", 0))
        out_tokens = int(usage.get("output_tokens", 0))
        return ModelResponse(
            text=text,
            provider=self.name,
            model=model,
            tier=request.tier,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            cost_usd=price(request.tier, in_tokens, out_tokens),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
