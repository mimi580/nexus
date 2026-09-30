# NEXUS AI

An autonomous B2B sales, sourcing and outreach platform for refurbished laptops,
used/refurbished iPhones, medical equipment, pharmaceutical products and
servers/IT, operating in East Africa and selected other markets.

NEXUS researches markets, finds buyers from real web sources and business
listings, finds their published contacts, qualifies and scores opportunities,
finds and vets suppliers and asks them for quotes, matches deals to real
supplier offers, calculates landed cost and margin, sends personalised
outreach and follow-ups, reads replies, drafts quotes for your approval and
learns from outcomes — inside a hard USD 200/month ceiling and a deterministic
policy layer.

**Start here: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — setup, deployment,
daily use, configuration and troubleshooting.

| Document | For |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Running NEXUS |
| [NEXUS_MASTER_SPEC.md](NEXUS_MASTER_SPEC.md) | The authoritative specification |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | How the code maps onto the spec |

## What it will not do

- Invent a company, contact, e-mail address, price, stock level, certificate or licence
- Send a price or quote without your approval
- Contact anyone who opted out, complained or bounced
- Make a financial or legal commitment, or execute a regulated transaction
- Exceed USD 200 in a month (the ceiling cannot be raised from configuration)
- Loop forever (every loop has iteration, duration, cost, action and stall limits)

## Five-minute look (no accounts needed)

```bash
git clone https://github.com/mimi580/nexus.git && cd nexus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
python -m app.cli simulate --days 10           # the whole loop against a simulated world
uvicorn app.api.main:app --port 8000           # dashboard at http://localhost:8000
```

Production runs as a Docker Compose stack (PostgreSQL, API, worker, Caddy for
HTTPS): see [Deploying to a server](docs/USER_GUIDE.md#6-deploying-to-a-server).

## Repository layout

```
app/
  agents/        sales agents, grounded research paths, reply drafting
  api/           FastAPI (login, operator endpoints, unsubscribe)
  audit/         append-only audit trail (escalations open review items)
  budget/        hard-ceiling ledger
  commercial/    supplier offer catalogue, price book, commercial settings
  core/          config and readiness, types, protocols, context, logging
  dashboard/     single-page control panel
  database/      SQLAlchemy models and session
  economics/     landed cost, margin, ranges
  evaluation/    strategy versioning and rollback
  execution/     SMTP sending, IMAP intake (replies, bounces, opt-outs)
  memory/        repositories and deduplication
  models/        model router, Anthropic and OpenAI-compatible providers, mocks
  orchestrator/  objective loop and loop controls
  policies/      policy engine, fact check, licence register
  review/        human review queue and outcomes
  scheduler/     durable idempotent jobs
  simulation/    fictional world and end-to-end runner
  tools/         web search providers, safe page fetcher, research service
  notify.py      Telegram / e-mail alerts
deploy/  docker/  docs/  migrations/  scripts/  tests/
```

## Tests

`python -m pytest -q` runs the suite. GitHub Actions runs it on every push, plus
a migration check, a simulation, and a full Docker stack against PostgreSQL.
