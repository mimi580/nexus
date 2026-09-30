# NEXUS AI v0.1 — Implementation Plan

This plan maps `NEXUS_MASTER_SPEC.md` onto the code in this repository. Section
numbers in brackets refer to the Master Spec.

## 1. Design position

Three rules shaped every decision below.

**The model proposes, deterministic code disposes.** LLMs do research,
classification, synthesis and writing. Whether anything may actually happen —
send, spend, commit, contact — is decided by ordinary Python with tests around
it. A prompt is never the place a permission lives. [9, 14]

**Nothing is invented.** Every commercial claim carries an evidence record with
a source, a kind and a verification status. Missing costs stay missing and
propagate as ranges and reduced confidence rather than being filled in. [10, 11]

**The platform runs without credentials.** Mock providers and a simulated world
are the default path, not a test harness bolted on afterwards. Production
adapters implement the same protocols and change nothing about the control
layer. [22]

## 2. Architecture

```
objective intake (API / CLI)
        │
   Orchestrator ── LoopController (iterations, duration, cost, actions, repeats, stall)
        │
   Agent registry ──── ModelRouter ──── providers (mock | anthropic | …)
        │                   │
   MemoryStore/TaskStore    └── BudgetController (reserve → commit/release)
        │
   PolicyEngine (12 rules) ── FactCheck
        │
   Execution (simulated email | SMTP adapter)
        │
   Audit + Evidence + Compliance events
        │
   Scheduler (durable jobs) → Learning → versioned Strategy/Experiment
```

Module boundaries: `app/core` (config, types, protocols, context, errors,
logging), `app/database` (schema/session), `app/memory` (repositories),
`app/models` (router + providers), `app/policies`, `app/budget`, `app/agents`,
`app/orchestrator`, `app/scheduler`, `app/evaluation`, `app/execution`,
`app/economics`, `app/simulation`, `app/api`, `app/dashboard`, `app/audit`.

## 3. Dependencies

Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 + pydantic-settings,
psycopg (PostgreSQL), httpx, pytest, uvicorn. Redis is configured but not yet
required; the scheduler is durable in PostgreSQL instead. Deliberately no agent
framework, no ORM-on-ORM, no queue broker at v0.1. [17]

## 4. Database

22 tables in `app/database/models.py` covering objectives, tasks, market
assessments, companies, contacts, products, suppliers, buyer requirements,
opportunities, stage transitions, interactions, messages, follow-ups,
strategies, experiments, outcomes, model runs, cost entries, budget periods,
evidence, compliance events, audit events, system state and scheduled jobs. [7, 10]

Deduplication is structural, not advisory: companies, suppliers and
opportunities carry a unique `dedupe_key`, tasks a unique `idempotency_key`,
messages a `dedupe_key` that the duplicate policy rule checks before any send.
Every stage change writes a `stage_transitions` row, so the lifecycle is
reconstructable. Timestamps are naive UTC everywhere so SQLite and PostgreSQL
behave identically.

## 5. Agent interfaces

`app/core/interfaces.py` defines `ModelProvider`, `ModelRouterProtocol`, `Tool`,
`Agent`, `MemoryStore`, `TaskStore`, `PolicyEngineProtocol`,
`BudgetControllerProtocol`, `SchedulerProtocol`, `Evaluator` and `AuditLogger`
as Protocols, with Pydantic models for `ModelRequest`, `ModelResponse`,
`ToolResult`, `EvidenceRecord`, `ActionRequest`, `PolicyResult` and
`AgentResult`. [3 of the build prompt]

Agents take a `RunContext` and a task input dict and return an `AgentResult`
carrying output, evidence, cost, notes and the next tasks to enqueue. They never
enqueue work themselves — the orchestrator does, through idempotency keys — so
each agent is independently testable.

The twelve agents from spec section 6 are implemented: market research,
prospect discovery, company intelligence, decision-maker discovery,
qualification, opportunity scoring, sourcing/matching, sales strategy, outreach,
response, follow-up and learning.

## 6. Model and tool abstraction

`ModelRouter` selects a logical tier (reasoning / bulk / critic) from task type
and complexity, then picks a provider by preference, observed failure rate and
observed latency. Two consecutive failures cool a provider down for several
calls; a failure releases its budget reservation and falls through to the next
provider; every attempt writes a `model_runs` row. Credentials only ever come
from `Settings`. [9]

## 7. Policy and risk

`PolicyEngine.evaluate(ActionRequest)` runs twelve rule families and returns
allow / block / escalate, with block winning over escalate:

- system: emergency stop, global pause, paused category, paused geography
- security: credential-shaped payloads
- destructive actions
- financial and legal commitments (escalate above a configurable threshold that
  defaults to zero — every commitment goes to a human)
- regulated: regulated transactions, unverified regulatory position before
  contacting a medical/pharma buyer, controlled/restricted products
- licence coverage: medical/pharma contact outside an active licence for the
  buyer's country and category escalates; a commitment outside coverage is
  blocked (`app/policies/licenses.py`, `licenses` table)
- communication: opt-out, bounce, missing address, company-level opt-out
- duplicate outbound message
- daily and per-contact rate limits
- follow-up caps and stop-on-reply
- fact validation (below)
- budget

`app/policies/fact_check.py` is the outbound content gate: banned medical
claims, regulatory claims without verification, relationship claims
("authorized distributor") without verification, and any figure of 10 or more
that is not in the evidence-backed fact set. Copy that fails is blocked, not
rewritten. [12, 13]

## 8. Budget

`BudgetController` implements reserve → commit/release against `cost_entries`
rows, per category and per month. `HARD_MONTHLY_CEILING_USD = 200` is a module
constant; the settings validator clamps any configured value down to it, so the
ceiling cannot be raised from configuration or environment. Reservations that
would breach a ceiling raise `BudgetExceeded` before the action runs; a commit
larger than its reservation is clamped to whatever headroom remains; exhausting
the period engages a hard stop that the orchestrator checks each iteration. [15]

## 9. Orchestrator and loop

Objective intake validates product categories, persists the objective and seeds
one market-research task per category. `run()` pulls the highest-priority due
task, checks emergency stop / pause / budget hard stop first, executes it and
enqueues whatever the agent returned. `LoopController` enforces max iterations,
duration, cost, actions and repeats-per-signature, and aborts on stalls
(iterations without progress). `recover()` requeues tasks left `running` by a
crashed process, so a restart cannot lose an objective. [8, 19]

## 10. Scheduler

Durable `scheduled_jobs` rows hold `next_run_at` and a `last_run_key` window
token, which makes jobs idempotent across restarts and double invocations. Jobs
enqueue keyed tasks rather than doing work inline: market-research refresh,
inbound processing, due follow-ups, opportunity monitoring, daily report,
weekly learning review, monthly budget review. A failing job is recorded and
rescheduled; it never stops the others. [17]

## 11. Learning

`metrics()` computes response rate, qualified-response rate, conversion,
pipeline margin, cost per qualified lead and breakdowns by country and product.
The learning agent proposes bounded parameter changes as new `strategies` rows
(inactive until explicitly activated) with an `experiments` record; activation
and rollback live in `app/evaluation/strategies.py`. No source-code
self-modification. [20]

## 12. Dashboard

A single self-contained page at `/` polling `/api/state`: budget with usage bar
and forecast, pipeline by stage, results metrics, blocked and escalated actions,
compliance events, failed tasks, and controls for run / pause / resume /
emergency stop. [18]

## 13. Testing

pytest covering: budget ceiling (including 300 sequential spends and overrun
clamping), policy rules, fact validation, economics, router fallback and
provider health, loop termination, agent behaviour (market re-ranking under
different weights, dedupe, refusing unsourced contacts, explainable scoring,
compliance gates, thin-margin drop), outreach and response scenarios, restart
recovery, scheduler idempotency and failure isolation, API, security, and a
full end-to-end simulation. [21]

## 14. Deployment

`docker/Dockerfile` (non-root, health check) plus `docker-compose.yml` with
PostgreSQL, Redis, a one-shot `alembic upgrade head` migration service, the API
and a background worker. Configuration is entirely environment-driven from
`.env`; `.env.example` documents every key and `.gitignore` excludes real
secrets. [16, 22]

## 15. Known gaps at v0.1

Real web research, CRM and browser tools are not implemented — prospect and
supplier data comes from the simulation fixtures behind the same interfaces. The
SMTP/API email adapter is a skeleton. Redis is unused. The API has no
authentication. These are listed with next steps in `README.md`.
