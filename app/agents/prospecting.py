"""Prospect discovery, company intelligence and decision-maker discovery."""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.interfaces import AgentResult
from app.core.types import EvidenceKind, ModelTier, OpportunityStage, utcnow
from app.database.models import Company, Contact, Opportunity


class ProspectDiscoveryAgent(BaseAgent):
    name = "prospect_discovery"
    task_type = "prospect_discovery"
    tier = ModelTier.BULK
    complexity = 0.4

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        category = task_input["product_category"]
        countries = task_input.get("countries") or []
        limit = int(task_input.get("limit", 6))

        prompt = (
            f"Find organisations plausibly buying {category} in {', '.join(countries) or 'the target markets'}. "
            "Return {'prospects':[{'name','domain','country','city','segment','size_indicator',"
            "'description','buying_signals':[],'source'}]}."
        )
        data, cost = self.ask(
            ctx, prompt, {"product_category": category, "countries": countries, "limit": limit}
        )

        created, duplicates, next_tasks, evidence = 0, 0, [], []
        for item in data.get("prospects", [])[:limit]:
            if not item.get("name"):
                continue
            company, is_new = ctx.memory.upsert_company(
                name=item["name"],
                domain=item.get("domain"),
                country=item.get("country"),
                city=item.get("city"),
                segment=item.get("segment"),
                size_indicator=item.get("size_indicator"),
                description=item.get("description", ""),
                buying_signals=item.get("buying_signals") or [],
                source=item.get("source"),
            )
            opportunity, opp_new = ctx.memory.create_opportunity(
                company_id=company.id,
                product_category=category,
                objective_id=ctx.objective_id,
            )
            if is_new:
                created += 1
            else:
                duplicates += 1
            evidence.append(
                self.evidence(
                    f"{company.name} is a candidate buyer of {category}",
                    source=item.get("source") or "model",
                    kind=EvidenceKind.UNVERIFIED_CLAIM,
                    confidence=0.4,
                    subject_type="company",
                    subject_id=company.id,
                )
            )
            if opp_new:
                next_tasks.append(
                    {
                        "agent": "company_intelligence",
                        "input": {"opportunity_id": opportunity.id},
                        "priority": 65,
                    }
                )
        self.persist_evidence(ctx, evidence)
        return self.ok(
            output={"created": created, "duplicates": duplicates},
            cost_usd=cost,
            evidence=evidence,
            next_tasks=next_tasks,
            notes=[f"{created} new companies, {duplicates} deduplicated"],
        )


class CompanyIntelligenceAgent(BaseAgent):
    name = "company_intelligence"
    task_type = "company_intelligence"
    tier = ModelTier.BULK
    complexity = 0.45

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        company = ctx.session.get(Company, opportunity.company_id)
        assert company is not None

        prompt = (
            f"Summarise what {company.name} does and any evidence of demand for "
            f"{opportunity.product_category}. Return {{'needs':[],'signals':[],'size_indicator',"
            "'notes','confidence','sources':[]}."
        )
        data, cost = self.ask(
            ctx,
            prompt,
            {
                "company_name": company.name,
                "domain": company.domain,
                "country": company.country,
                "product_category": opportunity.product_category,
                "buying_signals": company.buying_signals,
                "size_indicator": company.size_indicator,
            },
        )
        if data.get("size_indicator") and data["size_indicator"] != "unknown":
            company.size_indicator = data["size_indicator"]
        signals = list({*(company.buying_signals or []), *(data.get("signals") or [])})
        company.buying_signals = signals
        company.last_verified_at = utcnow()

        evidence = [
            self.evidence(
                f"{company.name}: {data.get('notes', '')}",
                source=(data.get("sources") or ["model"])[0],
                kind=EvidenceKind.UNVERIFIED_CLAIM,
                confidence=float(data.get("confidence", 0.4)),
                subject_type="company",
                subject_id=company.id,
            )
        ]
        self.persist_evidence(ctx, evidence)
        ctx.memory.transition(opportunity, OpportunityStage.RESEARCHED, "company profile captured")
        return self.ok(
            output={"needs": data.get("needs", []), "signals": signals},
            cost_usd=cost,
            evidence=evidence,
            next_tasks=[
                {
                    "agent": "decision_maker_discovery",
                    "input": {"opportunity_id": opportunity.id},
                    "priority": 60,
                }
            ],
        )


class DecisionMakerAgent(BaseAgent):
    name = "decision_maker_discovery"
    task_type = "decision_maker_discovery"
    tier = ModelTier.BULK
    complexity = 0.45
    MIN_CONFIDENCE = 0.35

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        company = ctx.session.get(Company, opportunity.company_id)
        assert company is not None

        prompt = (
            f"Identify the procurement-relevant role at {company.name} for "
            f"{opportunity.product_category}. Only return a contact if it is supported by a source; "
            "otherwise return an empty list. Return {'contacts':[{'full_name','role','email',"
            "'confidence','source','verified'}]}."
        )
        data, cost = self.ask(
            ctx,
            prompt,
            {
                "company_name": company.name,
                "domain": company.domain,
                "product_category": opportunity.product_category,
                "limit": 1,
            },
        )
        contacts = [
            c
            for c in data.get("contacts", [])
            if c.get("full_name") and c.get("source") and float(c.get("confidence", 0)) >= self.MIN_CONFIDENCE
        ]
        if not contacts:
            ctx.memory.transition(opportunity, OpportunityStage.STALE, "no sourced decision maker")
            return self.ok(
                output={"contacts": 0},
                cost_usd=cost,
                notes=["no contact met the evidence threshold; opportunity parked"],
            )

        item = contacts[0]
        evidence_record = ctx.memory.record_evidence(
            self.evidence(
                f"{item['full_name']} is {item.get('role')} at {company.name}",
                source=item["source"],
                kind=EvidenceKind.UNVERIFIED_CLAIM,
                confidence=float(item.get("confidence", 0.4)),
                subject_type="company",
                subject_id=company.id,
            )
        )
        contact = ctx.memory.upsert_contact(
            company_id=company.id,
            full_name=item["full_name"],
            role=item.get("role"),
            email=item.get("email"),
            confidence=float(item.get("confidence", 0.4)),
            source=item["source"],
            evidence_id=evidence_record.id,
            verified=bool(item.get("verified")),
        )
        opportunity.contact_id = contact.id
        ctx.session.flush()
        return self.ok(
            output={"contact_id": contact.id, "confidence": contact.confidence},
            cost_usd=cost,
            next_tasks=[
                {"agent": "qualification", "input": {"opportunity_id": opportunity.id}, "priority": 60}
            ],
        )
