"""Strategy versioning: activate, measure, roll back. Never self-modifying code."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexusError
from app.core.types import utcnow
from app.database.models import Experiment, Strategy


def active_strategy(session: Session, name: str) -> Strategy | None:
    return session.scalar(
        select(Strategy).where(Strategy.name == name, Strategy.active.is_(True)).order_by(Strategy.version.desc())
    )


def parameters(session: Session, name: str, default: dict | None = None) -> dict:
    strategy = active_strategy(session, name)
    return dict(strategy.parameters) if strategy else dict(default or {})


def propose(session: Session, name: str, params: dict, rationale: str = "") -> Strategy:
    latest = session.scalar(
        select(Strategy).where(Strategy.name == name).order_by(Strategy.version.desc())
    )
    strategy = Strategy(
        name=name,
        version=(latest.version + 1) if latest else 1,
        parameters=params,
        active=False,
        rationale=rationale,
    )
    session.add(strategy)
    session.flush()
    return strategy


def activate(session: Session, strategy_id: str) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise NexusError("strategy not found", strategy_id=strategy_id)
    for other in session.scalars(
        select(Strategy).where(Strategy.name == strategy.name, Strategy.active.is_(True))
    ):
        other.active = False
        other.retired_at = utcnow()
    strategy.active = True
    strategy.activated_at = utcnow()
    session.flush()
    return strategy


def rollback(session: Session, name: str) -> Strategy | None:
    """Reactivate the most recent previously-retired version."""
    current = active_strategy(session, name)
    candidates = list(
        session.scalars(
            select(Strategy).where(Strategy.name == name).order_by(Strategy.version.desc())
        )
    )
    previous = next((s for s in candidates if current is None or s.version < current.version), None)
    if previous is None:
        return None
    return activate(session, previous.id)


def conclude_experiment(session: Session, experiment_id: str, result: dict, decision: str) -> Experiment:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise NexusError("experiment not found", experiment_id=experiment_id)
    experiment.result = result
    experiment.decision = decision
    experiment.status = "concluded"
    session.flush()
    return experiment
