#!/usr/bin/env python3
"""Continuous background worker: orchestration pass + due scheduled jobs.

Restart-safe: every cycle recovers tasks left running by a previous process.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
import signal
import time

from sqlalchemy import select

from app.core.context import build_context
from app.core.logging import configure_logging, get_logger, log_event
from app.database.models import Objective
from app.database.session import session_scope
from app.orchestrator.orchestrator import Orchestrator
from app.scheduler.scheduler import Scheduler

logger = get_logger("nexus.worker")
_running = True


def _stop(*_args) -> None:
    global _running
    _running = False


def cycle() -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        orchestrator = Orchestrator(ctx)
        orchestrator.recover()
        results = []
        for objective in session.scalars(select(Objective).where(Objective.status == "active")):
            results.append(orchestrator.run(objective.id))
        jobs = Scheduler(ctx).run_due()
        return {"objectives": len(results), "jobs": len(jobs), "budget": ctx.budget.snapshot()["remaining_usd"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    configure_logging()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while _running:
        try:
            summary = cycle()
            log_event(logger, logging.INFO, "worker_cycle", **summary)
        except Exception as exc:  # a bad cycle never kills the worker
            log_event(logger, logging.ERROR, "worker_cycle_failed", error=str(exc))
        for _ in range(args.interval):
            if not _running:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
