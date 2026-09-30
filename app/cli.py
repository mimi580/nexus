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


def cmd_license(args: argparse.Namespace) -> int:
    from datetime import date

    from app.audit.logger import DbAuditLogger
    from app.policies.licenses import (
        LicenseError,
        add_license,
        as_dict,
        deactivate_license,
        list_licenses,
    )

    create_all(args.database_url)
    with session_scope() as session:
        try:
            if args.license_command == "add":
                lic = add_license(
                    session,
                    holder_name=args.holder,
                    country=args.country,
                    issuing_authority=args.authority,
                    license_number=args.number,
                    license_types=args.types,
                    product_categories=args.categories,
                    expires_on=args.expires,
                    valid_from=args.valid_from,
                    scope_notes=args.notes,
                    document_ref=args.document,
                    verification=args.verification,
                )
                DbAuditLogger(session).record(
                    "license_added", summary=f"{lic.country} licence {lic.license_number}",
                    decision="allow", actor="operator", license_id=lic.id,
                )
                print(json.dumps(as_dict(lic, date.today()), indent=2))
            elif args.license_command == "deactivate":
                lic = deactivate_license(session, args.license_id)
                DbAuditLogger(session).record(
                    "license_deactivated", summary=f"{lic.country} licence {lic.license_number}",
                    decision="allow", actor="operator", license_id=lic.id,
                )
                print(json.dumps(as_dict(lic, date.today()), indent=2))
            else:
                rows = [as_dict(lic, date.today()) for lic in list_licenses(session, args.all)]
                print(json.dumps(rows, indent=2))
        except LicenseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
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

    lic = sub.add_parser("license", help="manage the regulated-trade licence register")
    lic_sub = lic.add_subparsers(dest="license_command", required=True)
    lic_add = lic_sub.add_parser("add", help="register a licence")
    lic_add.add_argument("--holder", required=True, help="licensed entity name as it appears on the licence")
    lic_add.add_argument("--country", required=True)
    lic_add.add_argument("--authority", required=True, help="issuing authority, e.g. Pharmacy and Poisons Board")
    lic_add.add_argument("--number", required=True, help="licence number")
    lic_add.add_argument("--types", nargs="+", required=True, help="importer distributor wholesaler ...")
    lic_add.add_argument("--categories", nargs="+", required=True, help="medical_equipment pharmaceutical ...")
    lic_add.add_argument("--expires", required=True, help="YYYY-MM-DD")
    lic_add.add_argument("--valid-from", default=None, help="YYYY-MM-DD")
    lic_add.add_argument("--notes", default="", help="scope limits written on the licence")
    lic_add.add_argument("--document", default=None, help="where the licence copy is kept")
    lic_add.add_argument(
        "--verification", default="user_provided",
        choices=["user_provided", "document_checked", "registry_confirmed"],
    )
    lic_list = lic_sub.add_parser("list", help="list licences")
    lic_list.add_argument("--all", action="store_true", help="include deactivated licences")
    lic_off = lic_sub.add_parser("deactivate", help="stop relying on a licence")
    lic_off.add_argument("license_id")
    lic.set_defaults(func=cmd_license)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
