"""Command line entry points."""

from __future__ import annotations

import argparse
import json
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import create_all, session_scope


def cmd_init_db(args: argparse.Namespace) -> int:
    create_all(args.database_url)
    print(f"schema created at {args.database_url or get_settings().database_url}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from app.simulation.runner import run_simulation

    report = run_simulation(
        database_url=args.database_url, days=args.days, verbose=not args.quiet
    )
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from app.core.context import build_context
    from app.orchestrator.orchestrator import Orchestrator
    from app.scheduler.scheduler import Scheduler

    create_all()
    with session_scope() as session:
        ctx = build_context(session)
        orchestrator = Orchestrator(ctx)
        if args.objective:
            objective = orchestrator.create_objective(
                title=args.objective, product_categories=args.categories
            )
            objective_id = objective.id
        else:
            objective_id = args.objective_id
        summary = orchestrator.run(objective_id)
        jobs = Scheduler(ctx).run_due()
        print(json.dumps({"loop": summary, "jobs": jobs}, indent=2, default=str))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from app.agents.learning import metrics
    from app.core.context import build_context

    with session_scope() as session:
        ctx = build_context(session)
        print(json.dumps({"metrics": metrics(ctx, args.window), "budget": ctx.budget.snapshot()}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="nexus")
    parser.add_argument("--database-url", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    sim = sub.add_parser("simulate", help="run the full loop against the simulated world")
    sim.add_argument("--days", type=int, default=10)
    sim.add_argument("--quiet", action="store_true")
    sim.set_defaults(func=cmd_simulate)

    run = sub.add_parser("run", help="run one orchestration pass")
    run.add_argument("--objective", default=None)
    run.add_argument("--objective-id", default=None)
    run.add_argument("--categories", nargs="*", default=["refurbished_laptop"])
    run.set_defaults(func=cmd_run)

    rep = sub.add_parser("report")
    rep.add_argument("--window", type=int, default=30)
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
