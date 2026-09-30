"""Reusable agent-loop controls. No loop can run forever or overspend."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from app.core.ids import stable_key


@dataclass
class LoopLimits:
    max_iterations: int = 200
    max_duration_seconds: float = 900.0
    max_cost_usd: float = 5.0
    max_actions: int = 300
    max_retries_per_task: int = 3
    min_progress_iterations: int = 12  # iterations allowed without progress
    max_repeats_per_signature: int = 3


@dataclass
class LoopState:
    iterations: int = 0
    actions: int = 0
    cost_usd: float = 0.0
    progress_events: int = 0
    stall_iterations: int = 0
    started_at: float = field(default_factory=time.monotonic)
    signatures: Counter = field(default_factory=Counter)
    stop_reason: str | None = None
    escalations: list[str] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at


class LoopController:
    def __init__(self, limits: LoopLimits | None = None) -> None:
        self.limits = limits or LoopLimits()
        self.state = LoopState()

    def signature(self, agent: str, task_input: dict) -> str:
        return stable_key(agent, sorted(task_input.items(), key=lambda kv: kv[0]))

    def should_continue(self) -> bool:
        s, limits = self.state, self.limits
        if s.stop_reason:
            return False
        if s.iterations >= limits.max_iterations:
            s.stop_reason = "max_iterations"
        elif s.elapsed >= limits.max_duration_seconds:
            s.stop_reason = "max_duration"
        elif s.cost_usd >= limits.max_cost_usd:
            s.stop_reason = "max_cost"
        elif s.actions >= limits.max_actions:
            s.stop_reason = "max_actions"
        elif s.stall_iterations >= limits.min_progress_iterations:
            s.stop_reason = "no_progress"
        return s.stop_reason is None

    def is_repeat(self, signature: str) -> bool:
        return self.state.signatures[signature] >= self.limits.max_repeats_per_signature

    def record_iteration(self, signature: str, *, cost: float, progressed: bool, actions: int = 1) -> None:
        s = self.state
        s.iterations += 1
        s.actions += actions
        s.cost_usd += cost
        s.signatures[signature] += 1
        if progressed:
            s.progress_events += 1
            s.stall_iterations = 0
        else:
            s.stall_iterations += 1

    def stop(self, reason: str) -> None:
        self.state.stop_reason = reason

    def summary(self) -> dict:
        s = self.state
        return {
            "iterations": s.iterations,
            "actions": s.actions,
            "cost_usd": round(s.cost_usd, 6),
            "elapsed_seconds": round(s.elapsed, 3),
            "progress_events": s.progress_events,
            "stop_reason": s.stop_reason or "queue_empty",
            "escalations": s.escalations,
        }
