"""Configuration. All secrets come from the environment; nothing is hard-coded."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

HARD_MONTHLY_CEILING_USD = 200.0  # Spec section 15. Code may lower it, never raise it.

DEFAULT_CATEGORY_LIMITS = {
    "infrastructure": 20.0,
    "ai_primary": 45.0,
    "ai_secondary": 20.0,
    "research_data": 30.0,
    "email": 25.0,
    "storage": 5.0,
    "automation": 15.0,
    "testing_misc": 10.0,
    "reserve": 30.0,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    nexus_env: Literal["development", "production"] = "development"
    nexus_mode: Literal["simulation", "production"] = "simulation"

    database_url: str = "sqlite+pysqlite:///./data/nexus.sqlite3"
    redis_url: str | None = None

    budget_monthly_limit_usd: float = HARD_MONTHLY_CEILING_USD
    budget_category_limits_json: str | None = None

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    model_primary: str | None = None
    model_bulk: str | None = None
    model_critic: str | None = None

    email_provider: str = "simulated"
    email_sender_address: str | None = None
    email_sender_name: str | None = None
    email_api_key: str | None = None
    outreach_daily_limit: int = 40
    outreach_per_company_day_limit: int = 1
    outreach_max_followups: int = 3

    require_human_approval_above_usd: float = 0.0
    allow_regulated_autonomous_transactions: bool = False

    random_seed: int = 20260917

    @field_validator("budget_monthly_limit_usd")
    @classmethod
    def _cap_budget(cls, v: float) -> float:
        return min(float(v), HARD_MONTHLY_CEILING_USD)

    @property
    def category_limits(self) -> dict[str, float]:
        if not self.budget_category_limits_json:
            return dict(DEFAULT_CATEGORY_LIMITS)
        try:
            parsed = json.loads(self.budget_category_limits_json)
        except json.JSONDecodeError:
            return dict(DEFAULT_CATEGORY_LIMITS)
        return {str(k): float(v) for k, v in parsed.items()}

    @property
    def has_live_model_credentials(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key)

    def redacted(self) -> dict:
        """Safe for logs and the dashboard: secrets become presence flags."""
        data = self.model_dump()
        for key in ("anthropic_api_key", "openai_api_key", "email_api_key", "database_url"):
            data[key] = "set" if data.get(key) else "unset"
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
