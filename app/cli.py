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
                    regions=args.regions,
                    coverage_countries=args.covers,
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


def _ctx_session(args: argparse.Namespace):
    create_all(args.database_url)
    return session_scope()


def cmd_doctor(args: argparse.Namespace) -> int:
    """Readiness check, optionally with live connection tests."""
    settings = get_settings()
    report = {"mode": settings.nexus_mode, "env": settings.nexus_env, **settings.readiness(), "checks": {}}
    checks = report["checks"]
    try:
        create_all(args.database_url)
        with session_scope() as session:
            from sqlalchemy import text

            session.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - environment specific
        checks["database"] = f"FAILED: {exc}"
    if args.live:
        checks.update(_live_checks(settings))
    print(json.dumps(report, indent=2))
    failed = [k for k, v in checks.items() if str(v).startswith("FAILED")]
    return 1 if (report["missing"] and settings.nexus_mode == "production") or failed else 0


def _live_checks(settings) -> dict:  # pragma: no cover - needs real services
    import imaplib
    import smtplib
    import ssl

    checks: dict = {}
    if settings.search_provider != "none" and settings.search_api_key:
        from app.tools.search import build_search_provider

        try:
            hits = build_search_provider(settings).search("Kenya hospital tender medical equipment", count=3)
            checks["search"] = f"ok ({len(hits)} results)"
        except Exception as exc:
            checks["search"] = f"FAILED: {exc}"
    if settings.smtp_host and settings.smtp_username and settings.smtp_password:
        try:
            if settings.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
            with server:
                if settings.smtp_security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                server.login(settings.smtp_username, settings.smtp_password)
            checks["smtp_login"] = "ok"
        except Exception as exc:
            checks["smtp_login"] = f"FAILED: {exc}"
    if settings.imap_host and settings.imap_username and settings.imap_password:
        try:
            with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as client:
                client.login(settings.imap_username, settings.imap_password)
                client.select(settings.imap_folder)
            checks["imap_login"] = "ok"
        except Exception as exc:
            checks["imap_login"] = f"FAILED: {exc}"
    if settings.has_live_model_credentials:
        from app.core.interfaces import ModelRequest
        from app.core.types import ModelTier

        provider = None
        if settings.anthropic_api_key:
            from app.models.providers.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(settings)
        else:
            from app.models.providers.openai_compatible import OpenAICompatibleProvider

            provider = OpenAICompatibleProvider(settings)
        try:
            response = provider.complete(ModelRequest(
                task_type="doctor", prompt='Reply with {"ok": true}', tier=ModelTier.BULK, max_output_tokens=20,
            ))
            checks["model"] = f"ok ({response.model})"
        except Exception as exc:
            checks["model"] = f"FAILED: {exc}"
    return checks


def cmd_review(args: argparse.Namespace) -> int:
    from app.core.context import build_context
    from app.review import queue

    with _ctx_session(args) as session:
        ctx = build_context(session)
        if args.review_command == "decide":
            try:
                item = queue.decide(ctx, args.review_id, args.decision, args.note or "", actor="operator-cli")
            except queue.ReviewError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(queue.as_dict(item), indent=2))
        else:
            print(json.dumps([queue.as_dict(i) for i in queue.pending(session)], indent=2))
    return 0


def cmd_outcome(args: argparse.Namespace) -> int:
    from app.core.context import build_context
    from app.review import queue

    with _ctx_session(args) as session:
        ctx = build_context(session)
        try:
            outcome = queue.record_outcome(
                ctx, args.opportunity_id, args.result, revenue_usd=args.revenue, margin_usd=args.margin,
                note=args.note or "", actor="operator-cli",
            )
        except queue.ReviewError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"id": outcome.id, "result": outcome.result}))
    return 0


def cmd_catalogue(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.commercial import catalogue

    if args.catalogue_command == "template":
        sys.stdout.write(catalogue.template(args.kind))
        return 0
    with _ctx_session(args) as session:
        try:
            if args.catalogue_command == "import":
                result = catalogue.import_csv(session, Path(args.file).read_text(encoding="utf-8-sig"), args.kind)
                print(json.dumps(result, indent=2))
                return 0 if not result["errors"] else 2
            if args.catalogue_command == "deactivate":
                if args.kind == "offers":
                    row = catalogue.offer_as_dict(catalogue.deactivate_offer(session, args.item_id), session)
                else:
                    row = catalogue.price_as_dict(catalogue.deactivate_price_reference(session, args.item_id))
                print(json.dumps(row, indent=2))
                return 0
            if args.kind == "offers":
                rows = [catalogue.offer_as_dict(o, session) for o in catalogue.list_offers(session, args.category)]
            else:
                rows = [catalogue.price_as_dict(p) for p in catalogue.list_price_references(session, args.category)]
            print(json.dumps(rows, indent=2))
        except catalogue.CatalogueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    return 0


def cmd_settings(args: argparse.Namespace) -> int:
    from app.commercial import settings as commercial

    with _ctx_session(args) as session:
        if args.settings_command == "set":
            try:
                value = commercial.update(session, {args.key: json.loads(args.value)})
            except (json.JSONDecodeError, commercial.CommercialSettingsError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(value[args.key], indent=2))
        else:
            print(json.dumps(commercial.get(session), indent=2))
    return 0


def cmd_research(args: argparse.Namespace) -> int:  # pragma: no cover - needs a search key
    from app.tools.search import build_search_provider

    provider = build_search_provider(get_settings())
    if provider is None:
        print("error: set SEARCH_PROVIDER and SEARCH_API_KEY first", file=sys.stderr)
        return 2
    hits = provider.search(args.query, country=args.country, count=args.count)
    print(json.dumps([{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits], indent=2))
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    from app.core.context import build_context
    from app.notify import notify

    with _ctx_session(args) as session:
        ctx = build_context(session)
        delivered = notify(ctx, "Test notification", "NEXUS can reach you on this channel.")
        print(json.dumps({"channels": ctx.settings.notify_channel_list, "delivered": delivered}))
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
    lic_add.add_argument(
        "--regions", nargs="*", default=[], help="declared regional trading scope, e.g. EAC COMESA"
    )
    lic_add.add_argument("--covers", nargs="*", default=[], help="extra countries in the declared scope")
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

    doctor = sub.add_parser("doctor", help="check configuration and (with --live) connections")
    doctor.add_argument("--live", action="store_true", help="also test search, SMTP, IMAP and the model")
    doctor.set_defaults(func=cmd_doctor)

    review = sub.add_parser("review", help="the human review queue")
    review_sub = review.add_subparsers(dest="review_command", required=True)
    review_sub.add_parser("list")
    decide = review_sub.add_parser("decide")
    decide.add_argument("review_id")
    decide.add_argument("decision", choices=["approve", "reject", "resolve"])
    decide.add_argument("--note", default="")
    review.set_defaults(func=cmd_review)

    outcome = sub.add_parser("outcome", help="record a won/lost deal")
    outcome.add_argument("opportunity_id")
    outcome.add_argument("result", choices=["won", "lost"])
    outcome.add_argument("--revenue", type=float, default=None)
    outcome.add_argument("--margin", type=float, default=None)
    outcome.add_argument("--note", default="")
    outcome.set_defaults(func=cmd_outcome)

    cat = sub.add_parser("catalogue", help="supplier offers and the selling-price book")
    cat_sub = cat.add_subparsers(dest="catalogue_command", required=True)
    tpl = cat_sub.add_parser("template", help="print a CSV header to fill in")
    tpl.add_argument("kind", choices=["offers", "prices"])
    imp = cat_sub.add_parser("import", help="import a CSV (all rows or none)")
    imp.add_argument("kind", choices=["offers", "prices"])
    imp.add_argument("file")
    lst = cat_sub.add_parser("list")
    lst.add_argument("kind", choices=["offers", "prices"])
    lst.add_argument("--category", default=None)
    off = cat_sub.add_parser("deactivate")
    off.add_argument("kind", choices=["offers", "prices"])
    off.add_argument("item_id")
    cat.set_defaults(func=cmd_catalogue)

    st = sub.add_parser("settings", help="commercial settings (margins, order sizes, duties, markets)")
    st_sub = st.add_subparsers(dest="settings_command", required=True)
    st_sub.add_parser("show")
    st_set = st_sub.add_parser("set", help='e.g. settings set min_margin_pct \'{"pharmaceutical": 15}\'')
    st_set.add_argument("key")
    st_set.add_argument("value", help="JSON value")
    st.set_defaults(func=cmd_settings)

    rs = sub.add_parser("research", help="run one live search (tests SEARCH_API_KEY)")
    rs.add_argument("query")
    rs.add_argument("--country", default=None)
    rs.add_argument("--count", type=int, default=5)
    rs.set_defaults(func=cmd_research)

    nt = sub.add_parser("notify-test", help="send a test notification to the configured channels")
    nt.set_defaults(func=cmd_notify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
