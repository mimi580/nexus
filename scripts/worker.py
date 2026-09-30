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
import os
import signal
import tempfile
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
# The system temp folder (/tmp on Linux, %TEMP% on Windows).
HEARTBEAT = Path(os.environ.get("NEXUS_HEARTBEAT_FILE", Path(tempfile.gettempdir()) / "nexus-worker-heartbeat"))


def check_heartbeat(interval: int) -> int:
    """Exit 0 if the worker finished a cycle recently (for Docker health checks)."""
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except FileNotFoundError:
        print("no heartbeat yet")
        return 1
    limit = interval * 3 + 300
    print(f"last cycle {age:.0f}s ago (limit {limit}s)")
    return 0 if age < limit else 1


def _stop(*_args) -> None:
    global _running
    _running = False


def cycle() -> dict:
    with session_scope() as session:
        ctx = build_context(session)
        # Scheduled jobs first, so replies, follow-ups and research they enqueue
        # are executed in this same cycle.
        jobs = Scheduler(ctx).run_due()
        active = session.scalars(select(Objective.id).where(Objective.status == "active")).all()
        loop = Orchestrator(ctx).run(None)  # all due work, including replies with no objective
        return {
            "active_objectives": len(active),
            "jobs": len(jobs),
            "iterations": loop.get("iterations"),
            "stop_reason": loop.get("stop_reason"),
            "budget_remaining_usd": ctx.budget.snapshot()["remaining_usd"],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=int(os.environ.get("WORKER_INTERVAL_SECONDS", 300)))
    parser.add_argument("--check-heartbeat", action="store_true", help="health check: was there a recent cycle?")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()
    if args.check_heartbeat:
        return check_heartbeat(args.interval)
    configure_logging()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while _running:
        try:
            summary = cycle()
            log_event(logger, logging.INFO, "worker_cycle", **summary)
            HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT.touch()
        except Exception as exc:  # a bad cycle never kills the worker
            log_event(logger, logging.ERROR, "worker_cycle_failed", error=str(exc))
        if args.once:
            break
        for _ in range(args.interval):
            if not _running:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
