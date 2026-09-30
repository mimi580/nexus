from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.context import build_context
from app.database.base import Base
from app.database.session import get_engine, get_session_factory, reset_engine
import app.database.models  # noqa: F401


class Clock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 17, 9, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        nexus_env="development",
        nexus_mode="simulation",
        database_url="sqlite+pysqlite:///:memory:",
        anthropic_api_key=None,
        email_sender_name="NEXUS Sourcing",
    )


@pytest.fixture
def session(settings):
    reset_engine()
    engine = get_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        reset_engine()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ctx(session, settings, clock):
    return build_context(session, settings, clock=clock)
