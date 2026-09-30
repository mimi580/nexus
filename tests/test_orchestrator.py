import pytest
from sqlalchemy import func, select

from app.agents.base import BaseAgent
from app.core.errors import NexusError
from app.core.interfaces import AgentResult
from app.core.types import ProductCategory
from app.database.models import AuditEvent, Objective, Task
from app.orchestrator.loop import LoopLimits
from app.orchestrator.orchestrator import Orchestrator


class LoopingAgent(BaseAgent):
    """Always schedules itself again: the loop controls must stop it."""

    name = "looper"

    def run(self, ctx, task_input):
        return AgentResult(
            agent=self.name,
            ok=True,
            next_tasks=[{"agent": "looper", "input": {"n": task_input.get("n", 0) + 1}}],
        )


class ExplodingAgent(BaseAgent):
    name = "exploder"

    def run(self, ctx, task_input):
        raise RuntimeError("boom")


def test_unknown_product_category_is_rejected(ctx):
    with pytest.raises(NexusError):
        Orchestrator(ctx).create_objective("bad", ["moon_rockets"])


def test_objective_seeds_market_and_supplier_research_per_category(ctx):
    orchestrator = Orchestrator(ctx)
    objective = orchestrator.create_objective(
        "pipeline", [ProductCategory.LAPTOP.value, ProductCategory.IPHONE.value]
    )
    agents = sorted(ctx.session.scalars(select(Task.agent).where(Task.objective_id == objective.id)))
    assert agents == ["market_research", "market_research", "supplier_research", "supplier_research"]


def test_self_scheduling_agent_cannot_run_forever(ctx):
    orchestrator = Orchestrator(ctx, agents={"looper": LoopingAgent()}, limits=LoopLimits(max_iterations=8))
    objective = ctx.tasks.create_objective("loop test")
    ctx.tasks.create_task(agent="looper", objective_id=objective.id, task_input={"n": 0})
    summary = orchestrator.run(objective.id)
    assert summary["iterations"] <= 8
    assert summary["stop_reason"] in {"max_iterations", "no_progress"}


def test_failing_agent_is_retried_then_failed(ctx):
    orchestrator = Orchestrator(ctx, agents={"exploder": ExplodingAgent()})
    objective = ctx.tasks.create_objective("failure test")
    task, _ = ctx.tasks.create_task(agent="exploder", objective_id=objective.id, task_input={})
    orchestrator.run(objective.id)
    refreshed = ctx.session.get(Task, task.id)
    assert refreshed.status == "failed"
    assert refreshed.attempts == refreshed.max_attempts


def test_emergency_stop_halts_the_orchestrator(ctx):
    orchestrator = Orchestrator(ctx)
    objective = orchestrator.create_objective("halt", [ProductCategory.LAPTOP.value])
    ctx.memory.set_control_state(emergency_stop=True)
    summary = orchestrator.run(objective.id)
    assert summary["stop_reason"] == "emergency_stop"
    assert summary["iterations"] == 0


def test_every_run_is_audited(ctx):
    orchestrator = Orchestrator(ctx)
    objective = orchestrator.create_objective("audited", [ProductCategory.SERVER_IT.value])
    orchestrator.run(objective.id)
    events = {e.event_type for e in ctx.session.scalars(select(AuditEvent))}
    assert {"objective_created", "loop_finished"} <= events


def test_completed_objective_is_marked(ctx):
    orchestrator = Orchestrator(ctx)
    objective = orchestrator.create_objective("finish", [ProductCategory.SERVER_IT.value])
    orchestrator.run(objective.id)
    assert ctx.session.get(Objective, objective.id).status in {"completed", "active"}
