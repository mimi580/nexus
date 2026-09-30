"""Objective intake -> decomposition -> execution -> evaluation -> replanning."""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.agents.registry import build_registry
from app.core.context import RunContext
from app.core.errors import BudgetExceeded, EscalationRequired, NexusError, PolicyViolation
from app.core.ids import stable_key
from app.core.logging import get_logger, log_event
from app.core.types import ProductCategory
from app.database.models import Objective, Task
from app.orchestrator.loop import LoopController, LoopLimits

logger = get_logger("nexus.orchestrator")


class Orchestrator:
    def __init__(
        self,
        ctx: RunContext,
        agents: dict[str, BaseAgent] | None = None,
        limits: LoopLimits | None = None,
    ) -> None:
        self.ctx = ctx
        self.agents = agents or build_registry()
        self.limits = limits or LoopLimits()

    # ------------------------------------------------------------- intake
    def create_objective(
        self,
        title: str,
        product_categories: list[str],
        description: str = "",
        constraints: dict[str, Any] | None = None,
    ) -> Objective:
        valid = {c.value for c in ProductCategory}
        unknown = [c for c in product_categories if c not in valid]
        if unknown:
            raise NexusError("unknown product categories", unknown=unknown)

        objective = self.ctx.tasks.create_objective(
            title=title,
            description=description,
            constraints=(constraints or {}) | {"product_categories": product_categories},
        )
        for category in product_categories:
            self.ctx.tasks.create_task(
                agent="market_research",
                objective_id=objective.id,
                task_input={"product_category": category},
                priority=90,
                idempotency_key=stable_key("market_research", objective.id, category),
            )
        self.ctx.audit.record(
            "objective_created",
            summary=title,
            objective_id=objective.id,
            decision="allow",
            categories=product_categories,
        )
        return objective

    # ---------------------------------------------------------- execution
    def recover(self) -> int:
        """Restart safety: tasks left running by a crash go back on the queue."""
        recovered = self.ctx.tasks.recover_stale_running()
        if recovered:
            self.ctx.audit.record("restart_recovery", summary=f"{recovered} tasks requeued", decision="allow")
        return recovered

    def _spawn(self, task: Task, next_tasks: list[dict]) -> int:
        spawned = 0
        for spec in next_tasks:
            agent = spec["agent"]
            task_input = spec.get("input", {})
            _, created = self.ctx.tasks.create_task(
                agent=agent,
                objective_id=task.objective_id,
                task_input=task_input,
                priority=int(spec.get("priority", 50)),
                parent_id=task.id,
                idempotency_key=stable_key(agent, sorted(task_input.items(), key=lambda kv: kv[0])),
            )
            spawned += int(created)
        return spawned

    def run_once(self, task: Task, controller: LoopController) -> bool:
        """Execute a single task. Returns True when it made progress."""
        agent = self.agents.get(task.agent)
        self.ctx.task_id = task.id
        self.ctx.objective_id = task.objective_id
        if agent is None:
            self.ctx.tasks.mark_failed(task, f"unknown agent: {task.agent}", retry=False)
            return False

        signature = controller.signature(task.agent, task.input or {})
        if controller.is_repeat(signature):
            self.ctx.tasks.mark_blocked(task, "repeated action suppressed")
            self.ctx.audit.record(
                "duplicate_action_suppressed", summary=task.agent, task_id=task.id, decision="block"
            )
            controller.record_iteration(signature, cost=0.0, progressed=False)
            return False

        self.ctx.tasks.mark_running(task)
        progressed = False
        cost = 0.0
        try:
            result = agent.run(self.ctx, task.input or {})
            cost = result.cost_usd
            if result.ok:
                spawned = self._spawn(task, result.next_tasks)
                self.ctx.tasks.mark_done(
                    task, {"output": result.output, "notes": result.notes, "spawned": spawned}
                )
                progressed = True
            else:
                self.ctx.tasks.mark_failed(task, result.error or "agent reported failure")
        except EscalationRequired as exc:
            self.ctx.tasks.mark_blocked(task, exc.message, escalated=True)
            controller.state.escalations.append(f"{task.agent}: {exc.message}")
            self.ctx.audit.record(
                "task_escalated", summary=exc.message, task_id=task.id, decision="escalate"
            )
        except PolicyViolation as exc:
            self.ctx.tasks.mark_blocked(task, exc.message)
            self.ctx.audit.record("task_blocked", summary=exc.message, task_id=task.id, decision="block")
        except BudgetExceeded as exc:
            self.ctx.tasks.mark_blocked(task, exc.message)
            controller.stop("budget_exhausted")
            self.ctx.audit.record("budget_stop", summary=exc.message, task_id=task.id, decision="block")
        except Exception as exc:  # unexpected: retry within max_attempts
            self.ctx.tasks.mark_failed(task, f"{type(exc).__name__}: {exc}")
            log_event(logger, logging.ERROR, "task_failed", task_id=task.id, agent=task.agent, error=str(exc))
        finally:
            self.ctx.task_id = None
            controller.record_iteration(signature, cost=cost, progressed=progressed)
        return progressed

    def run(self, objective_id: str | None = None, limits: LoopLimits | None = None) -> dict:
        controller = LoopController(limits or self.limits)
        settings = self.ctx.settings
        if settings.nexus_mode == "production" and not settings.production_ready:
            missing = settings.readiness()["missing"]
            self.ctx.audit.record(
                "not_production_ready",
                summary="production run refused: " + "; ".join(missing),
                decision="block",
            )
            controller.stop("not_production_ready")
            return {**controller.summary(), "missing": missing}
        self.recover()
        while controller.should_continue():
            if self.ctx.budget.hard_stopped():
                controller.stop("budget_hard_stop")
                break
            control = self.ctx.memory.control_state()
            if control.get("emergency_stop"):
                controller.stop("emergency_stop")
                break
            if control.get("paused"):
                controller.stop("paused")
                break
            task = self.ctx.tasks.next_pending(objective_id, now=self.ctx.now)
            if task is None:
                break
            self.run_once(task, controller)

        summary = controller.summary()
        # Objectives are standing pipelines: an empty queue today does not end
        # them (follow-ups, replies and weekly research keep arriving). Only the
        # operator closes an objective.
        self.ctx.session.flush()
        self.ctx.audit.record(
            "loop_finished", summary=summary["stop_reason"], objective_id=objective_id, decision="allow", **summary
        )
        return summary
