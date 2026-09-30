# NEXUS_MASTER_SPEC.md

## 1. Status

**Project:** NEXUS AI \| **Version:** 0.1 \| **Status:** Authoritative
Blueprint

NEXUS is an autonomous commercial-intelligence platform. Its first
production capability is B2B sales, sourcing and outreach. Future Career
agents must reuse the same core platform.

**Initial products:** refurbished laptops; used/refurbished iPhones;
medical equipment; pharmaceutical products; servers/IT equipment.
**Budget:** hard maximum USD 200/month. **Autonomy:** fully autonomous
within deterministic policy, compliance, financial, security and
communication boundaries.

## 2. Vision

NEXUS continuously discovers commercially viable opportunities,
identifies buyers and suppliers, qualifies demand, evaluates economics
and risk, conducts permitted outreach, follows up, records outcomes and
improves strategy from evidence.

Core loop: **Objective → Observe → Research → Reason → Plan → Policy
Check → Act → Observe Result → Evaluate → Learn → Re-plan.**

## 3. Commercial Scope

### Products

-   Refurbished laptops: Dell Latitude, HP EliteBook/ProBook, Lenovo
    ThinkPad and similar business-class machines.
-   Used/refurbished iPhones: used, professionally refurbished, and
    legitimately available new/excess stock.
-   Medical equipment: diagnostic, laboratory, monitoring, hospital and
    clinical equipment.
-   Pharmaceutical products: legally tradable medicines, permitted OTC
    products, medical consumables and institutional supplies.
-   Servers/IT: servers, networking, storage and enterprise
    infrastructure.

### Markets

-   Medical, pharmaceutical and server/IT: Africa, with country
    priorities discovered dynamically from demand, procurement,
    purchasing power, competition, regulation, logistics, payment risk,
    supplier availability, margins and historical conversion.
-   Used/refurbished iPhones: East Africa and other evidence-supported
    African markets; selected Central/Eastern/Southeastern European
    lower-income markets.
-   Laptops: Africa initially, including schools, universities, NGOs,
    businesses, institutions, hospitals, IT resellers and distributors.

NEXUS must not permanently hard-code a "best country" ranking.

## 4. Sales Model

Use a **hybrid direct-sales + sourcing + brokerage model**. Support:
direct inventory sales; buyer-first sourcing; supplier-first buyer
matching; brokered transactions. Inventory does not have to be owned by
the user.

For serious opportunities calculate selling price, acquisition cost,
shipping, insurance where applicable, duties/taxes where applicable,
transaction costs, gross profit and gross margin. Where inputs are
uncertain, provide ranges and confidence.

## 5. Architecture

    YOU / COMMAND
          ↓
    ORCHESTRATOR
          ↓
    AGENT LOOP ENGINE ←→ MODEL ROUTER
          ↓
    SALES INTELLIGENCE ←→ MEMORY / DATABASE
          ↓
    POLICY + RISK + BUDGET
          ↓
    EXECUTION (browser/email/CRM/etc.)
          ↓
    RESULTS / AUDIT
          ↓
    EVALUATION + LEARNING
          ↺

Core modules: Orchestrator, Agent Loop Engine, Model Router, Memory,
Database, Scheduler, Budget Controller, Policy/Risk Engine,
Evaluation/Learning, Audit, Execution, Dashboard.

## 6. Sales Agents

1.  **Market Research:** demand, procurement signals, competitors,
    barriers and market opportunities.
2.  **Prospect Discovery:** companies, organizations, buying signals and
    deduplication.
3.  **Company Intelligence:** business, geography, size indicators,
    needs, signals and evidence.
4.  **Decision-Maker Discovery:** procurement, purchasing, IT,
    operations, supply-chain, medical/pharma procurement and appropriate
    directors/owners. Never fabricate identities or contacts.
5.  **Qualification:** product fit, need evidence, order potential,
    timing, geography, decision-maker confidence, budget indicators,
    supplier feasibility, regulatory and payment risk.
6.  **Opportunity Scoring:** explainable scoring using buyer
    probability, fit, order size, margin, supplier availability, market
    attractiveness, payment risk, regulation, logistics, competition and
    time-to-close.
7.  **Sourcing/Matching:** suppliers, prices, quantities, condition,
    documents, lead times, shipping, payment terms and reliability.
8.  **Sales Strategy:** segment, value proposition, channel, message,
    timing and follow-up.
9.  **Outreach:** personalized accurate email/business messages,
    proposals, RFQ responses and follow-ups.
10. **Response:** interested, information request, price request, RFQ,
    negotiation, not interested, wrong contact, unsubscribe, complaint,
    suspicious, regulatory issue, other.
11. **Follow-up:** bounded sequences, stop on opt-out/complaint/closure
    and avoid duplicates.
12. **Learning:** measure conversion, margin, response, cost, market,
    segment, product, message and model performance.

## 7. Opportunity Lifecycle

`DISCOVERED → RESEARCHED → QUALIFIED → SCORED → TARGETED → OUTREACH → RESPONSE/FOLLOW-UP → NEGOTIATION → COMMERCIAL REVIEW → WON/LOST/STALE → LEARNING`

Every transition is persisted.

## 8. Agent Loops

### Reactive

Incoming response, new lead, supplier update or relevant market event.
\### Operational Daily prospecting, outreach, follow-up and pipeline
management. \### Learning Days/weeks: evaluate outcomes, calibrate
scoring, compare strategies and model performance. \### Strategic
Weeks/months: reallocate markets, products, segments and strategies.

Every loop requires max iterations, duration, cost, actions, retries,
minimum progress, stop conditions and escalation conditions. Deadlocks,
repeated actions and runaway costs must terminate or re-plan.

## 9. Model Strategy

Use a provider-neutral **Model Router**. Initial logical tiers: primary
reasoning model, low-cost/bulk model, critic/verification model. Routing
considers task type, complexity, context, latency, cost, reliability and
historical performance. Providers must be replaceable; credentials must
come from secrets/environment variables.

LLMs handle research, reasoning, classification, synthesis, planning,
writing and strategic analysis. Deterministic code controls state,
permissions, budgets, rate limits, deduplication, scheduling,
idempotency, audit and risk.

## 10. Memory and Evidence

Persist objectives, tasks, companies, contacts, products, suppliers,
buyer requirements, opportunities, interactions, messages, follow-ups,
strategies, experiments, outcomes, model runs, costs, budgets, evidence,
compliance events and audit events.

Evidence records should distinguish verified facts, unverified claims,
inferences, user-provided facts and model hypotheses. Store source,
retrieval time, confidence and verification status. Never invent people,
companies, prices, stock, certifications, regulations or references.

## 11. Product Schema

Support category, name, brand/model, condition, specification, quantity,
acquisition cost, target price, minimum margin, currency, location,
supplier, warranty, shipping/payment terms, regulatory metadata,
documentation, availability, evidence and last verified date. Missing
data remains unknown.

## 12. Medical/Pharmaceutical Controls

NEXUS may conduct commercial research and lead generation, but must
verify relevant regulatory requirements and documentation, avoid
unsupported medical claims, never invent approvals/certifications, flag
controlled/restricted products, respect import/export requirements and
escalate high-risk regulatory situations. No binding or legally
significant regulated transaction should bypass the policy/compliance
layer.

## 13. Communication Controls

Track sender identity, message history, frequency, opt-outs, bounces,
complaints and duplicates. Do not fabricate inventory, certifications,
relationships, references or prices. Respect applicable communication
rules and opt-outs.

## 14. Autonomy and Policy

NEXUS is fully autonomous for routine permitted work: research,
prospecting, scoring, CRM, routine outreach, follow-up, supplier
research and reporting. Hard boundaries apply to financial commitments,
legal commitments, high-risk regulated transactions, secrets,
destructive actions, unusual spending and fraud/security indicators.

Policy flow: **AI decision → deterministic policy/risk check → budget
check → permitted execution or block/escalate → audit.**

## 15. Budget

Hard maximum: **USD 200/month**. Planning allocation: infrastructure
\$20; primary AI \$45; secondary AI \$20; research/data \$30; email
\$25; database/storage \$5; browser/automation \$15; testing/misc \$10;
reserve \$30. These are ceilings/planning targets, not required
spending.

Implement a ledger, category spend, current period, forecast, reserved
funds, remaining funds, action cost estimate and hard stop. No paid
action bypasses the controller.

## 16. Security

Never hard-code or log API keys, passwords, OAuth secrets or private
keys. Use environment variables/secret management, least privilege,
separate development/production credentials, rotation and audit. Include
`.env.example` and `.gitignore`.

## 17. Database / Technology

Preferred: Python 3.12+, FastAPI, PostgreSQL, SQLAlchemy, Alembic,
Pydantic, Redis where useful, background workers, Docker, pytest,
structured JSON logs and a modern web dashboard. Keep business logic
separate from external providers.

## 18. Dashboard

Show objectives, leads, qualified leads, opportunities, responses,
outreach, pipeline, estimated margin, spending, budget remaining, tasks,
decisions, blocked actions, errors and learning metrics. Provide
pause/resume, category/geography pause and emergency stop.

## 19. Reliability

Implement retries with backoff, provider fallback, persistent state,
idempotency, duplicate prevention, health checks, recovery after
restart, structured errors and graceful degradation.

## 20. Learning

Measure response rate, qualified-response rate, conversion,
revenue/pipeline, gross margin, cost per qualified lead,
country/segment/product/message/model performance. Strategy changes are
versioned, evaluated, monitored and reversible. Do not implement
uncontrolled self-modifying source code.

## 21. Testing

Provide unit, integration, scenario, autonomy, security, budget and
recovery tests. Required scenarios include interested buyer, price
request, opt-out, duplicate lead, supplier failure, budget exhaustion,
regulatory uncertainty, AI failure and deadlock.

## 22. Simulation and Production

Build a complete simulation mode using fake prospects, suppliers,
prices, emails, responses and budgets. The same agent loop must run
without credentials. Production adapters are enabled only after
simulation tests pass, while retaining policy, budget, audit, rate and
compliance controls.

## 23. Definition of Done

NEXUS v0.1 must boot, accept objectives, plan and persist tasks,
research markets, discover/deduplicate prospects, research companies and
contacts, qualify and score opportunities, source/match products,
calculate economics, generate and simulate/send outreach, process
responses, schedule follow-ups, learn from outcomes, enforce
budget/policy, audit actions, recover from failures, expose a dashboard,
run continuously and document deployment.

## 24. Guiding Principle

**Optimize for commercially useful outcomes, not activity.** NEXUS
should be willing to pursue, change strategy, change market, change
product or stop when evidence supports that decision.
