"""Supplier sourcing/matching plus deterministic deal economics."""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.core.context import RunContext
from app.core.errors import UnknownInput
from app.core.interfaces import AgentResult
from app.core.types import EvidenceKind, ModelTier, Money, OpportunityStage, ProductCategory
from app.database.models import Company, Opportunity
from app.economics.calculator import DealInputs, compute_economics
from app.commercial import catalogue
from app.commercial import settings as commercial
from app.core.ids import stable_key

AWAITING_OFFER = "awaiting supplier offer"
AWAITING_PRICE = "awaiting selling price reference"


class SourcingAgent(BaseAgent):
    name = "sourcing"
    task_type = "sourcing"
    tier = ModelTier.BULK
    complexity = 0.5

    def _park(self, ctx: RunContext, opportunity: Opportunity, marker: str, reason: str, review_key: str) -> AgentResult:
        """Hold an opportunity until the operator fills a catalogue gap.

        One review item per gap (not per opportunity); the catalogue_recheck job
        resumes every parked opportunity once the gap is filled.
        """
        opportunity.blocked_reason = marker
        ctx.memory.transition(opportunity, OpportunityStage.COMMERCIAL_REVIEW, reason)
        ctx.audit.record(
            "catalogue_gap",
            summary=reason + " — add it to the catalogue to resume parked opportunities",
            decision="escalate",
            objective_id=ctx.objective_id,
            task_id=ctx.task_id,
            review_key=review_key,
            reasons=[reason],
        )
        next_tasks = []
        if marker == AWAITING_OFFER:
            # Go and find suppliers for this line now (at most once a day).
            next_tasks.append({
                "agent": "supplier_research",
                "input": {"product_category": opportunity.product_category, "trigger": ctx.now.strftime("%Y-%m-%d")},
                "priority": 66,
            })
        return self.ok(output={"parked": marker}, notes=[reason], next_tasks=next_tasks)

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        company = ctx.session.get(Company, opportunity.company_id)
        qual = opportunity.qualification or {}
        category = opportunity.product_category
        simulation = ctx.settings.nexus_mode == "simulation"
        settings = commercial.get(ctx.session)
        stated_qty = qual.get("order_potential_units")
        quantity = int(stated_qty or commercial.typical_order_qty(ctx.session, category, simulation))
        quantity_basis = "buyer signal" if stated_qty else "typical order size (assumption)"

        if simulation:
            prompt = (
                f"Find supply options for {category}. Return {{'offers':[{{'supplier_name','country',"
                "'unit_cost_usd','unit_cost_high_usd','quantity_available','condition','lead_time_days',"
                "'shipping_cost_usd','payment_terms','documents':[],'reliability','source'}]}."
            )
            data, cost = self.ask(ctx, prompt, {"product_category": category})
            offers = data.get("offers") or []
        else:
            # Production: acquisition costs come only from the operator's catalogue.
            offers = catalogue.current_offers(ctx.session, category, ctx.now.date())
            cost = 0.0

        def covers(offer: dict) -> bool:
            available = offer.get("quantity_available")
            moq = offer.get("moq")
            return (available is None or int(available) >= quantity) and (moq is None or int(moq) <= quantity)

        viable = [o for o in offers if covers(o)] or offers
        if not viable:
            if not simulation:
                return self._park(ctx, opportunity, AWAITING_OFFER, f"no current supplier offer for {category}",
                                  stable_key("catalogue_gap", "offer", category))
            ctx.memory.transition(opportunity, OpportunityStage.STALE, "no supply found")
            return self.ok(output={"offers": 0}, cost_usd=cost, notes=["no supplier could cover this line"])

        best = min(viable, key=lambda o: float(o["unit_cost_usd"]) / max(float(o.get("reliability", 0.5)), 0.1))
        supplier = ctx.memory.upsert_supplier(
            name=best["supplier_name"],
            country=best.get("country"),
            categories=[category],
            reliability_score=float(best.get("reliability", 0.5)),
            lead_time_days=best.get("lead_time_days"),
            payment_terms=best.get("payment_terms"),
            documents=best.get("documents") or [],
        )

        # --- compliance gate before anything commercial happens -------------
        required = set(settings["required_documents"].get(category, []))
        held = {d.lower() for d in (best.get("documents") or [])}
        missing = sorted(d for d in required if d.lower() not in held)
        regulatory_checked = not missing
        if missing:
            ctx.memory.record_compliance(
                opportunity_id=opportunity.id,
                product_category=category,
                flag="missing_documentation",
                severity="high",
                detail=f"missing: {', '.join(missing)}",
            )
            opportunity.blocked_reason = f"regulatory documentation missing: {', '.join(missing)}"
            opportunity.compliance_flags = list({*(opportunity.compliance_flags or []), "missing_documentation"})
            ctx.memory.transition(
                opportunity, OpportunityStage.COMMERCIAL_REVIEW, "regulatory documentation missing"
            )
            ctx.audit.record(
                "compliance_block",
                summary=f"{category} sourcing blocked pending documentation",
                decision="escalate",
                objective_id=ctx.objective_id,
                task_id=ctx.task_id,
                opportunity_id=opportunity.id,
                missing=missing,
            )
            return self.ok(
                output={"blocked": True, "missing_documents": missing},
                cost_usd=cost,
                notes=["escalated: regulated line without documentation"],
            )

        # --- economics ------------------------------------------------------
        country = company.country if company else None
        if simulation:
            from app.simulation.fixtures import UNIT_ECONOMICS

            price_low, price_high = UNIT_ECONOMICS.get(category, {}).get("unit_price", (None, None))
            price_basis = "simulated market reference"
        else:
            reference = catalogue.price_reference(ctx.session, category, country, ctx.now.date())
            if reference is None:
                return self._park(
                    ctx, opportunity, AWAITING_PRICE,
                    f"no current selling-price reference for {category}" + (f" in {country}" if country else ""),
                    stable_key("catalogue_gap", "price", category, country or ""),
                )
            price_low, price_high = reference.unit_price_low_usd, reference.unit_price_high_usd
            price_basis = f"price book: {reference.basis} ({reference.source})"
        if price_low is None:
            return self.fail("no reference selling price available; refusing to invent one", cost_usd=cost)
        try:
            economics = compute_economics(
                DealInputs(
                    quantity=quantity,
                    selling_price=Money(low=price_low, high=price_high, confidence=0.5, basis=price_basis),
                    acquisition_cost=Money(
                        low=float(best["unit_cost_usd"]),
                        high=float(best.get("unit_cost_high_usd", best["unit_cost_usd"])),
                        confidence=0.7,
                        basis="supplier quote",
                    ),
                    shipping=(
                        Money.exact(float(best["shipping_cost_usd"]), basis="supplier quote")
                        if best.get("shipping_cost_usd") is not None
                        else None
                    ),
                    duties_taxes_pct=commercial.duty_rate(ctx.session, country, simulation),
                    transaction_cost_pct=float(settings["transaction_cost_pct"]),
                )
            )
        except UnknownInput as exc:
            return self.fail(f"economics unavailable: {exc.message}", cost_usd=cost)

        min_margin = float(task_input.get("min_margin_pct") or settings["min_margin_pct"].get(category, 12.0))
        opportunity.supplier_id = supplier.id
        opportunity.economics = economics.summary() | {
            "unit_cost_usd": float(best["unit_cost_usd"]),
            "quantity": quantity,
            "lead_time_days": best.get("lead_time_days"),
            "condition": best.get("condition"),
            "min_margin_pct": min_margin,
            "quantity_basis": quantity_basis,
            "offer_id": best.get("offer_id"),
            "supplier_source": best.get("source"),
        }
        opportunity.blocked_reason = None
        opportunity.estimated_value_usd = economics.revenue.mid
        opportunity.estimated_margin_usd = economics.gross_profit.mid
        ctx.session.flush()

        evidence = [
            self.evidence(
                f"supplier quote {best['supplier_name']}: {best['unit_cost_usd']} USD/unit",
                source=best.get("source", "supplier"),
                kind=EvidenceKind.UNVERIFIED_CLAIM,
                confidence=0.55,
                subject_type="opportunity",
                subject_id=opportunity.id,
            )
        ]
        self.persist_evidence(ctx, evidence)

        if economics.gross_margin_pct_high < min_margin:
            ctx.memory.transition(
                opportunity,
                OpportunityStage.LOST,
                f"margin {economics.gross_margin_pct_high}% below minimum {min_margin}%",
            )
            return self.ok(
                output={"economics": opportunity.economics, "pursued": False},
                cost_usd=cost,
                evidence=evidence,
                notes=["dropped on economics"],
            )

        ctx.memory.transition(opportunity, OpportunityStage.TARGETED, "supply matched and economics acceptable")
        return self.ok(
            output={"economics": opportunity.economics, "pursued": True, "regulatory_checked": regulatory_checked},
            cost_usd=cost,
            evidence=evidence,
            next_tasks=[
                {
                    "agent": "sales_strategy",
                    "input": {"opportunity_id": opportunity.id, "regulatory_checked": regulatory_checked},
                    "priority": 56,
                }
            ],
        )


class SalesStrategyAgent(BaseAgent):
    name = "sales_strategy"
    task_type = "sales_strategy"
    tier = ModelTier.REASONING
    complexity = 0.7

    def run(self, ctx: RunContext, task_input: dict) -> AgentResult:
        opportunity = ctx.session.get(Opportunity, task_input["opportunity_id"])
        if opportunity is None:
            return self.fail("opportunity not found")
        company = ctx.session.get(Company, opportunity.company_id)
        prompt = (
            "Choose the sales approach for this opportunity. Return {'segment','value_proposition',"
            "'channel','message_angle','timing','followup_cadence_days'}."
        )
        data, cost = self.ask(
            ctx,
            prompt,
            {
                "product_category": opportunity.product_category,
                "segment": company.segment if company else None,
                "country": company.country if company else None,
            },
        )
        qual = dict(opportunity.qualification or {})
        qual["strategy"] = data
        opportunity.qualification = qual
        ctx.session.flush()
        return self.ok(
            output=data,
            cost_usd=cost,
            next_tasks=[
                {
                    "agent": "outreach",
                    "input": {
                        "opportunity_id": opportunity.id,
                        "regulatory_checked": bool(task_input.get("regulatory_checked", False)),
                    },
                    "priority": 54,
                }
            ],
        )
