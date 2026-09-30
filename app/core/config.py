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


SECRET_FIELDS = (
    "anthropic_api_key",
    "openai_api_key",
    "email_api_key",
    "database_url",
    "search_api_key",
    "smtp_password",
    "imap_password",
    "unsubscribe_secret",
    "telegram_bot_token",
    "dashboard_password",
)


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

    openai_base_url: str = "https://api.openai.com/v1"
    openai_model_primary: str | None = None
    openai_model_bulk: str | None = None
    openai_model_critic: str | None = None

    # --- research tools --------------------------------------------------
    search_provider: Literal["none", "brave", "tavily", "serper"] = "none"
    search_api_key: str | None = None
    search_cost_per_query_usd: float = 0.005
    fetch_user_agent: str = "NEXUS-research/0.2 (+contact via sender address)"
    fetch_max_bytes: int = 1_500_000
    fetch_timeout_seconds: float = 20.0
    research_max_queries_per_task: int = 3
    research_max_pages_per_task: int = 4

    # --- email -------------------------------------------------------------
    email_provider: str = "simulated"
    email_sender_address: str | None = None
    email_sender_name: str | None = None
    email_reply_to: str | None = None
    email_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    imap_host: str | None = None
    imap_port: int = 993
    imap_username: str | None = None
    imap_password: str | None = None
    imap_folder: str = "INBOX"

    # --- sender identity (required in production; appears in every email) --
    business_name: str | None = None
    business_postal_address: str | None = None
    public_base_url: str | None = None
    unsubscribe_secret: str | None = None

    # --- operator notifications ---------------------------------------------
    notify_channels: str = "log"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    operator_email: str | None = None
    notify_max_per_hour: int = 10

    # --- dashboard / API access ---------------------------------------------
    dashboard_username: str | None = None
    dashboard_password: str | None = None
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

    @property
    def notify_channel_list(self) -> list[str]:
        return [c.strip().lower() for c in self.notify_channels.split(",") if c.strip()]

    def readiness(self) -> dict[str, list[str]]:
        """What production operation still needs. Simulation needs nothing."""
        missing: list[str] = []
        warnings: list[str] = []
        if not self.has_live_model_credentials:
            missing.append("ANTHROPIC_API_KEY or OPENAI_API_KEY (no live model; mock models are never used in production)")
        if self.search_provider == "none" or not self.search_api_key:
            missing.append("SEARCH_PROVIDER and SEARCH_API_KEY (prospects must come from real sources)")
        if not (self.email_sender_address and self.email_sender_name):
            missing.append("EMAIL_SENDER_ADDRESS and EMAIL_SENDER_NAME")
        if not (self.business_name and self.business_postal_address):
            missing.append("BUSINESS_NAME and BUSINESS_POSTAL_ADDRESS (sender identification in every email)")
        if self.email_provider != "smtp" or not (self.smtp_host and self.smtp_username and self.smtp_password):
            missing.append("EMAIL_PROVIDER=smtp with SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD")
        if not (self.dashboard_username and self.dashboard_password):
            missing.append("DASHBOARD_USERNAME and DASHBOARD_PASSWORD")
        if not self.unsubscribe_secret or len(self.unsubscribe_secret) < 24:
            missing.append("UNSUBSCRIBE_SECRET (random, 24+ characters)")
        if not (self.imap_host and self.imap_username and self.imap_password):
            warnings.append("IMAP not configured: replies, bounces and opt-outs will not be read automatically")
        if not self.public_base_url:
            warnings.append("PUBLIC_BASE_URL not set: emails carry a reply-to-unsubscribe line but no one-click link")
        if self.notify_channel_list == ["log"]:
            warnings.append("NOTIFY_CHANNELS=log only: you will not be alerted about items awaiting review")
        return {"missing": missing, "warnings": warnings}

    @property
    def production_ready(self) -> bool:
        return not self.readiness()["missing"]

    def redacted(self) -> dict:
        """Safe for logs and the dashboard: secrets become presence flags."""
        data = self.model_dump()
        for key in SECRET_FIELDS:
            data[key] = "set" if data.get(key) else "unset"
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
