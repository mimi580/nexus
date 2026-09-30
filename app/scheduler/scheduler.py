"""Durable, idempotent scheduler. Restart-safe: next_run_at lives in the database."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select

from app.core.context import RunContext
from app.core.ids import stable_key
from app.core.logging import get_logger, log_event
from app.database.models import ScheduledJob
from app.scheduler.jobs import DEFAULT_JOBS

logger = get_logger("nexus.scheduler")

JobFn = Callable[[RunContext], dict]


class Scheduler:
    def __init__(self, ctx: RunContext, jobs: dict[str, tuple[JobFn, int]] | None = None) -> None:
        self.ctx = ctx
        self.jobs = jobs or DEFAULT_JOBS

    def ensure_registered(self, now: datetime | None = None) -> None:
        moment = now or self.ctx.now
        for name, (_, interval) in self.jobs.items():
            existing = self.ctx.session.scalar(select(ScheduledJob).where(ScheduledJob.name == name))
            if existing is None:
                self.ctx.session.add(
                    ScheduledJob(name=name, interval_seconds=interval, next_run_at=moment)
                )
        self.ctx.session.flush()

    def run_due(self, now: datetime | None = None) -> list[dict]:
        moment = now or self.ctx.now
        self.ensure_registered(moment)
        ran: list[dict] = []
        control = self.ctx.memory.control_state()
        if control.get("emergency_stop") or control.get("paused"):
            return ran

        rows = list(
            self.ctx.session.scalars(
                select(ScheduledJob).where(
                    ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= moment
                )
            )
        )
        for row in rows:
            fn, interval = self.jobs[row.name]
            run_key = stable_key(row.name, moment.strftime("%Y-%m-%dT%H"), str(interval))
            if row.last_run_key == run_key:
                continue  # already ran in this window
            try:
                result = fn(self.ctx)
                row.last_status = "ok"
            except Exception as exc:  # a failing job never stops the scheduler
                result = {"error": str(exc)}
                row.last_status = "error"
                log_event(logger, logging.ERROR, "job_failed", job=row.name, error=str(exc))
            row.last_run_at = moment
            row.last_run_key = run_key
            row.next_run_at = moment + timedelta(seconds=interval)
            self.ctx.session.flush()
            ran.append({"job": row.name, "status": row.last_status, "result": result})
        return ran
