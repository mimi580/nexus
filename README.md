# NEXUS AI v0.1

An autonomous commercial-intelligence platform. Its first capability is B2B
sales, sourcing and outreach across refurbished laptops, used/refurbished
iPhones, medical equipment, pharmaceutical products and servers/IT.

It discovers markets, finds and deduplicates prospects, researches companies and
contacts, qualifies and scores opportunities, matches suppliers, calculates
landed cost and margin, writes and sends outreach, processes replies, schedules
follow-ups and learns from outcomes — inside a hard USD 200/month ceiling and a
deterministic policy layer.

`NEXUS_MASTER_SPEC.md` is the authoritative specification.
`docs/IMPLEMENTATION_PLAN.md` explains how the code maps onto it.

## What it will not do

- Send a message containing a fact it cannot evidence (prices, stock,
  certifications, approvals, relationships)
- Contact anyone who opted out, complained or bounced
- Make a financial or legal commitment without a human
- Execute a regulated medical/pharmaceutical transaction autonomously
- Exceed the monthly budget ceiling, which cannot be raised from configuration
- Loop forever: every loop has iteration, duration, cost, action and stall limits

## Quick start (no credentials needed)

```bash
git clone <your-repo> nexus && cd nexus
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # defaults run in simulation mode on SQLite

python -m app.cli init-db
python -m pytest -q
python -m app.cli simulate --days 10
uvicorn app.api.main:app --reload --port 8000   # dashboard at http://localhost:8000
```

## Simulation

```bash
python -m app.cli simulate --days 10
# or, writing to a file database you can inspect afterwards:
python scripts/simulate.py --days 10 --database-url "sqlite+pysqlite:///./data/simulation.sqlite3"
```

One command runs the whole loop against a fake world: objective → market
research → prospecting → company intelligence → decision makers → qualification
→ scoring → supplier match → economics → outreach → simulated reply →
classification → follow-up or closure → learning. It prints a JSON report with
the pipeline by stage, message counts, evidence and compliance events,
escalations, blocked actions, budget usage and learning metrics.

## Everyday commands

```bash
python -m app.cli init-db                                  # create the schema (dev)
python -m app.cli run --objective "Laptop pipeline in East Africa" \
                      --categories refurbished_laptop       # one orchestration pass
python -m app.cli report --window 30                        # metrics + budget snapshot
python scripts/worker.py --interval 300                     # continuous background loop
```

## Licence register (medical and pharmaceutical)

Regulated trade is gated on the licences you actually hold. Register each one:

```bash
python -m app.cli license add \
  --holder "Your Company Ltd" --country Kenya \
  --authority "Pharmacy and Poisons Board" --number "PPB/XXXX/2026" \
  --types importer distributor \
  --categories medical_equipment pharmaceutical \
  --valid-from 2026-01-01 --expires 2027-12-31 \
  --document "where the licence copy is stored"
python -m app.cli license list
python -m app.cli license deactivate <licence id>
```

How NEXUS uses it:

- Outreach to a medical/pharma buyer goes out only when an active, unexpired
  licence covers that buyer's country and category. Otherwise it escalates for
  your review (rule R-REG-04); you may have a partner route.
- A regulated financial or legal commitment outside licence coverage is blocked
  (R-REG-05). Inside coverage it still goes to you — a licence never makes a
  regulated transaction autonomous.
- Emails may say "licensed importer and distributor in <country>" only when the
  register covers that deal; the fact check blocks licence claims otherwise.
- A daily job raises a compliance alert 45 days before expiry and again at expiry.

The same operations are available over the API (`GET/POST /api/licenses`,
`POST /api/licenses/{id}/deactivate`). Simulation runs seed a clearly fictional
Kenya licence so both covered and uncovered markets are exercised.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | liveness + mode |
| GET | `/api/state` | objectives, pipeline, tasks, budget, blocked actions, compliance, metrics |
| POST | `/api/objectives` | create an objective (`title`, `product_categories`, `description`) |
| POST | `/api/run` | run one orchestration pass plus due scheduled jobs |
| GET/POST | `/api/licenses` | list or register licences |
| POST | `/api/licenses/{id}/deactivate` | stop relying on a licence |
| POST | `/api/control` | `paused`, `emergency_stop`, `paused_categories`, `paused_geographies` |
| GET | `/` | dashboard |

Valid product categories: `refurbished_laptop`, `used_iphone`,
`medical_equipment`, `pharmaceutical`, `server_it`.

## Configuration

Everything comes from the environment; see `.env.example` for the full list.
The ones that matter:

| Key | Meaning |
| --- | --- |
| `NEXUS_MODE` | `simulation` (mock providers, no sends) or `production` |
| `DATABASE_URL` | PostgreSQL in production, SQLite for local work |
| `BUDGET_MONTHLY_LIMIT_USD` | clamped to 200 in code; lower values are honoured |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | absent means mock providers |
| `EMAIL_PROVIDER` + sender/API keys | `simulated` until an adapter is wired |
| `OUTREACH_DAILY_LIMIT`, `OUTREACH_PER_COMPANY_DAY_LIMIT`, `OUTREACH_MAX_FOLLOWUPS` | communication limits |
| `REQUIRE_HUMAN_APPROVAL_ABOVE_USD` | `0` means every financial commitment escalates |
| `ALLOW_REGULATED_AUTONOMOUS_TRANSACTIONS` | leave `false` |

## Production deployment

1. Fill `.env` from `.env.example`: `NEXUS_ENV=production`, a PostgreSQL
   `DATABASE_URL`, and production-only credentials separate from development.
2. `docker compose build`
3. `docker compose up -d db redis` and wait for the health checks.
4. `docker compose up migrate` — runs `alembic upgrade head`.
5. `docker compose up -d api worker` — API on 8000 with a `/health` check, worker
   running orchestration and scheduled jobs every five minutes.
6. Keep `NEXUS_MODE=simulation` for the first production run to confirm the
   deployed build behaves, then switch to `production` once an email adapter is
   implemented and reviewed.
7. Back up PostgreSQL (`pg_dump`) on a schedule; the database holds all state,
   so recovery is a restore plus a container restart — the orchestrator requeues
   anything left running.
8. Put the API behind a reverse proxy with TLS and authentication before
   exposing it. There is no auth in v0.1.

Schema changes: `alembic revision --autogenerate -m "description"` then
`alembic upgrade head`.

## Repository layout

```
app/
  agents/        twelve sales agents + registry
  api/           FastAPI app
  audit/         append-only audit trail
  budget/        hard-ceiling ledger
  core/          config, types, protocols, context, errors, logging
  dashboard/     control panel
  database/      SQLAlchemy models + session
  economics/     landed cost, margin, ranges
  evaluation/    strategy versioning and rollback
  execution/     email layer (simulated + adapter skeleton)
  memory/        repositories and deduplication
  models/        router + providers
  orchestrator/  objective loop and loop controls
  policies/      policy engine + fact validation
  scheduler/     durable idempotent jobs
  simulation/    fixtures + end-to-end runner
docker/  docs/  migrations/  scripts/  tests/
```

## Limitations at v0.1

- No live research tools. Prospects, suppliers and prices come from
  `app/simulation/fixtures.py` behind the same interfaces a real tool would
  implement. Nothing in this repository has contacted a real company.
- The SMTP/API email adapter raises `NotImplementedError`; only the simulated
  provider sends.
- No authentication on the API or dashboard.
- Redis is configured but unused; the scheduler is single-process and
  database-driven.
- Anthropic is the only live model adapter written.

## Next steps

1. Implement a web-research tool behind the `Tool` protocol and feed real
   sources into the evidence layer, replacing fixture-backed discovery.
2. Wire a real email provider, with bounce and complaint webhooks feeding
   `Contact.bounced` and the opt-out path.
3. Add authentication and per-user audit attribution to the API.
4. Move the scheduler to a worker queue once more than one worker is needed.
5. Add a human review queue UI for escalations and quotations, which currently
   surface only as audit events.
