"""OpenAI-compatible chat-completions adapter.

Works with OpenAI and any service exposing the same API (set OPENAI_BASE_URL).
Like every provider, it only ever sees the ModelProvider protocol's inputs and
never decides anything about permissions.
"""

from __future__ import annotations

import time

import httpx

from app.core.config import Settings
from app.core.errors import ProviderError, ProviderUnavailable
from app.core.interfaces import ModelRequest, ModelResponse
from app.core.types import ModelTier
from app.models.providers.base import BaseProvider, price, render_user_message


class OpenAICompatibleProvider(BaseProvider):
    name = "openai"

    def __init__(self, settings: Settings, timeout: float = 60.0) -> None:
        if not settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not configured")
        self._api_key = settings.openai_api_key
        self.url = settings.openai_base_url.rstrip("/") + "/chat/completions"
        self.models = {
            ModelTier.REASONING: settings.openai_model_primary or "gpt-4.1",
            ModelTier.BULK: settings.openai_model_bulk or "gpt-4.1-mini",
            ModelTier.CRITIC: settings.openai_model_critic or "gpt-4.1-mini",
        }
        self.timeout = timeout

    def complete(self, request: ModelRequest) -> ModelResponse:
        model = self.models[request.tier]
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": render_user_message(request)})
        payload: dict = {"model": model, "messages": messages, "max_tokens": request.max_output_tokens}
        if request.expects_json:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        try:
            response = httpx.post(
                self.url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("openai-compatible request failed", error=str(exc)) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable("openai-compatible provider unavailable", status=response.status_code)
        if response.status_code >= 400:
            raise ProviderError("openai-compatible provider rejected the request", status=response.status_code)
        data = response.json()
        choices = data.get("choices") or [{}]
        text = (choices[0].get("message") or {}).get("content") or ""
        usage = data.get("usage", {})
        in_tokens = int(usage.get("prompt_tokens", 0))
        out_tokens = int(usage.get("completion_tokens", 0))
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
