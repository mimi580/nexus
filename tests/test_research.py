"""Grounded research: sources in, verified facts out, inventions dropped."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.agents.market_research import MarketResearchAgent
from app.agents.prospecting import CompanyIntelligenceAgent, DecisionMakerAgent, ProspectDiscoveryAgent
from app.core.config import Settings
from app.core.context import build_context
from app.core.interfaces import ModelRequest, ModelResponse
from app.core.types import OpportunityStage, ProductCategory
from app.database.models import Company, Contact, CostEntry, MarketAssessment, SourceDocument
from app.models.providers.base import BaseProvider
from app.models.router import ModelRouter
from app.tools.fetch import FetchRefused, PageFetcher, StaticFetcher, extract_emails, html_to_text, is_public_host
from app.tools.research import ResearchService, appears
from app.tools.search import SearchHit, StaticSearch

MEDICAL = ProductCategory.MEDICAL.value


class ScriptedProvider(BaseProvider):
    """Returns a fixed JSON payload per task type, and records what it was shown."""

    name = "scripted"

    def __init__(self, replies: dict[str, dict]) -> None:
        self.replies = replies
        self.seen: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.seen.append(request)
        return ModelResponse(
            text=json.dumps(self.replies.get(request.task_type, {})),
            provider=self.name, model="scripted", tier=request.tier, cost_usd=0.0001,
        )


def _ctx(session, clock, replies, search=None, fetcher=None):
    settings = Settings(
        _env_file=None, nexus_env="production", nexus_mode="production",
        database_url="sqlite+pysqlite:///:memory:", search_provider="brave", search_api_key="test-key",
    )
    ctx = build_context(session, settings, clock=clock, research=False)
    provider = ScriptedProvider(replies)
    router = ModelRouter(session, ctx.budget, settings)
    router.register(provider, preference=90)
    ctx.router = router
    ctx.research = ResearchService(session, settings, ctx.budget, search or StaticSearch(), fetcher or StaticFetcher(), clock)
    return ctx, provider


HOSPITAL_HIT = SearchHit(
    title="Tender: Supply of diagnostic imaging equipment - Kisumu Specialist Hospital",
    url="https://tenders.example.go.ke/notice/4411",
    snippet="Kisumu Specialist Hospital invites bids for the supply and installation of two ultrasound machines.",
    rank=1,
)
CLINIC_HIT = SearchHit(
    title="Nakuru Heart Clinic expands cardiology unit",
    url="https://news.example.co.ke/nakuru-heart",
    snippet="Nakuru Heart Clinic will add a cath lab and ECG monitoring in 2026.",
    rank=2,
)


# --------------------------------------------------------------------- prospects


def test_only_organisations_named_in_a_source_are_accepted(session, clock):
    search = StaticSearch({"Kenya": [HOSPITAL_HIT, CLINIC_HIT]})
    ctx, provider = _ctx(session, clock, {}, search=search)
    docs = ctx.research.search("Kenya hospital tender medical equipment")
    ids = {doc.url: doc.id for doc in docs}
    provider.replies["prospect_discovery"] = {
        "prospects": [
            {"name": "Kisumu Specialist Hospital", "source_id": ids[HOSPITAL_HIT.url],
             "evidence_quote": "invites bids for the supply and installation of two ultrasound machines",
             "segment": "public hospital"},
            {"name": "Nakuru Heart Clinic", "source_id": ids[CLINIC_HIT.url], "evidence_quote": "fabricated quote"},
            {"name": "Imaginary Medical Centre", "source_id": ids[HOSPITAL_HIT.url]},
            {"name": "Kisumu Specialist Hospital", "source_id": "src_does_not_exist"},
        ]
    }
    result = ProspectDiscoveryAgent().run(ctx, {"product_category": MEDICAL, "countries": ["Kenya"], "limit": 6})
    assert result.ok
    assert result.output["created"] == 2
    assert result.output["rejected"] == 2
    names = set(ctx.session.scalars(select(Company.name)))
    assert names == {"Kisumu Specialist Hospital", "Nakuru Heart Clinic"}
    kisumu = ctx.session.scalar(select(Company).where(Company.name == "Kisumu Specialist Hospital"))
    assert kisumu.country == "Kenya"
    assert kisumu.source == HOSPITAL_HIT.url
    assert kisumu.buying_signals and "ultrasound" in kisumu.buying_signals[0]
    nakuru = ctx.session.scalar(select(Company).where(Company.name == "Nakuru Heart Clinic"))
    assert nakuru.buying_signals == []  # the quote was not in the source


def test_the_model_sees_the_sources(session, clock):
    search = StaticSearch({"Kenya": [HOSPITAL_HIT]})
    ctx, provider = _ctx(session, clock, {"prospect_discovery": {"prospects": []}}, search=search)
    ProspectDiscoveryAgent().run(ctx, {"product_category": MEDICAL, "countries": ["Kenya"]})
    shown = json.dumps(provider.seen[-1].context)
    assert "Kisumu Specialist Hospital" in shown


def test_searches_are_budgeted_and_cached(session, clock):
    search = StaticSearch({"hospital": [HOSPITAL_HIT]})
    ctx, _ = _ctx(session, clock, {}, search=search)
    ctx.research.search("Kenya hospital tender")
    ctx.research.search("Kenya hospital tender")
    assert search.queries == ["Kenya hospital tender"]
    spent = ctx.session.scalar(
        select(func.sum(CostEntry.amount_usd)).where(CostEntry.category == "research_data", CostEntry.state == "committed")
    )
    assert spent == pytest.approx(ctx.settings.search_cost_per_query_usd)


# --------------------------------------------------------------------- company


def test_company_profile_uses_only_quotes_found_in_sources(session, clock):
    fetcher = StaticFetcher({
        "https://kisumuspecialist.example/": ("Kisumu Specialist Hospital", "A 180-bed referral hospital. We are commissioning a new radiology wing."),
    })
    search = StaticSearch({
        "official website": [SearchHit("Kisumu Specialist Hospital | Home", "https://kisumuspecialist.example/", "Referral hospital", 1)],
    })
    ctx, provider = _ctx(session, clock, {}, search=search, fetcher=fetcher)
    company, _ = ctx.memory.upsert_company(name="Kisumu Specialist Hospital", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, MEDICAL)

    def reply_with_real_ids():
        page = ctx.session.scalar(select(SourceDocument).where(SourceDocument.kind == "page"))
        return {
            "summary": "Referral hospital",
            "size_indicator": "180 beds", "size_quote": "A 180-bed referral hospital", "size_source_id": page.id,
            "signals": [
                {"quote": "commissioning a new radiology wing", "source_id": page.id},
                {"quote": "buying 40 ventilators", "source_id": page.id},
            ],
        }

    class LateReply(dict):
        def get(self, key, default=None):
            return reply_with_real_ids() if key == "company_intelligence" else super().get(key, default)

    provider.replies = LateReply()
    result = CompanyIntelligenceAgent().run(ctx, {"opportunity_id": opportunity.id})
    assert result.ok, result.error
    assert company.domain == "kisumuspecialist.example"
    assert company.size_indicator == "180 beds"
    assert company.buying_signals == ["commissioning a new radiology wing"]
    assert opportunity.stage == OpportunityStage.RESEARCHED.value


def test_aggregator_sites_are_not_taken_as_the_website(session, clock):
    search = StaticSearch({
        "official website": [SearchHit("Kisumu Specialist Hospital - LinkedIn", "https://www.linkedin.com/company/ksh", "", 1)],
    })
    ctx, _ = _ctx(session, clock, {"company_intelligence": {"signals": []}}, search=search)
    company, _ = ctx.memory.upsert_company(name="Kisumu Specialist Hospital", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, MEDICAL)
    CompanyIntelligenceAgent().run(ctx, {"opportunity_id": opportunity.id})
    assert company.domain is None


# --------------------------------------------------------------------- contacts

CONTACT_PAGE = (
    "Contact us\nProcurement: Ms Wanjiru Kamau, Head of Supply Chain, w.kamau@ksh.example\n"
    "General enquiries: info@ksh.example\n"
)


def _contact_ctx(session, clock, reply):
    fetcher = StaticFetcher({"https://ksh.example/contact": ("Contact", CONTACT_PAGE)})
    ctx, provider = _ctx(session, clock, {}, fetcher=fetcher)
    company, _ = ctx.memory.upsert_company(name="Kisumu Specialist Hospital", domain="ksh.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, MEDICAL)

    class Late(dict):
        def get(self, key, default=None):
            if key != "decision_maker_discovery":
                return default
            page = ctx.session.scalar(select(SourceDocument).where(SourceDocument.url == "https://ksh.example/contact"))
            return {**reply, "source_id": page.id if page else None}

    provider.replies = Late()
    return ctx, opportunity


def test_named_contact_with_published_email_is_accepted(session, clock):
    ctx, opportunity = _contact_ctx(
        session, clock, {"full_name": "Wanjiru Kamau", "role": "Head of Supply Chain", "email": "w.kamau@ksh.example"}
    )
    result = DecisionMakerAgent().run(ctx, {"opportunity_id": opportunity.id})
    contact = ctx.session.get(Contact, result.output["contact_id"])
    assert contact.full_name == "Wanjiru Kamau" and contact.email == "w.kamau@ksh.example"
    assert contact.source == "https://ksh.example/contact"
    assert result.output["named"] is True


def test_invented_email_falls_back_to_the_published_role_mailbox(session, clock):
    ctx, opportunity = _contact_ctx(
        session, clock, {"full_name": "Wanjiru Kamau", "role": "Head", "email": "wanjiru@gmail.com"}
    )
    result = DecisionMakerAgent().run(ctx, {"opportunity_id": opportunity.id})
    contact = ctx.session.get(Contact, result.output["contact_id"])
    assert contact.email == "info@ksh.example"
    assert contact.full_name == "Procurement office"
    assert result.output["named"] is False


def test_name_not_on_the_page_is_not_used(session, clock):
    ctx, opportunity = _contact_ctx(
        session, clock, {"full_name": "John Invented", "role": "CEO", "email": "w.kamau@ksh.example"}
    )
    result = DecisionMakerAgent().run(ctx, {"opportunity_id": opportunity.id})
    contact = ctx.session.get(Contact, result.output["contact_id"])
    assert contact.email == "w.kamau@ksh.example"
    assert contact.full_name == "Procurement office"


def test_no_published_address_means_no_contact(session, clock):
    fetcher = StaticFetcher({"https://quiet.example/contact": ("Contact", "Call us on the number below.")})
    ctx, _ = _ctx(session, clock, {}, fetcher=fetcher)
    company, _ = ctx.memory.upsert_company(name="Quiet Clinic", domain="quiet.example", country="Kenya")
    opportunity, _ = ctx.memory.create_opportunity(company.id, MEDICAL)
    DecisionMakerAgent().run(ctx, {"opportunity_id": opportunity.id})
    assert ctx.session.scalar(select(func.count()).select_from(Contact)) == 0
    assert opportunity.stage == OpportunityStage.STALE.value


# --------------------------------------------------------------------- markets


def test_market_scores_only_for_configured_countries(session, clock):
    search = StaticSearch({"demand": [HOSPITAL_HIT]})
    ctx, _ = _ctx(session, clock, {
        "market_research": {"markets": [
            {"country": "Kenya", "factors": {"demand": 0.8, "payment_risk": 3.0}, "rationale": "tenders", "source_ids": []},
            {"country": "Atlantis", "factors": {"demand": 1.0}},
        ]}
    }, search=search)
    result = MarketResearchAgent().run(ctx, {"product_category": MEDICAL})
    assert result.ok, result.error
    rows = ctx.session.scalars(select(MarketAssessment)).all()
    assert [r.country for r in rows] == ["Kenya"]
    assert rows[0].factors["payment_risk"] == 1.0  # clamped


# --------------------------------------------------------------------- tools


def test_html_extraction_and_email_discovery():
    title, text, links, mailtos = html_to_text(
        "<html><head><title>Clinic</title><script>var x='a@b.com'</script></head>"
        "<body><p>Write to <a href='mailto:Orders@Clinic.example?subject=hi'>us</a></p>"
        "<p>logo@2x.png</p></body></html>"
    )
    assert title == "Clinic" and "a@b.com" not in text
    assert extract_emails(text, mailtos) == ["orders@clinic.example"]


def test_fetcher_refuses_private_and_non_http_targets():
    fetcher = PageFetcher(Settings(_env_file=None))
    for url in ("file:///etc/passwd", "http://127.0.0.1/admin", "http://localhost:8000/"):
        with pytest.raises(FetchRefused):
            fetcher.fetch(url)
    assert is_public_host("localhost") is False


def test_grounding_matcher_ignores_case_and_punctuation():
    assert appears("Kisumu Specialist Hospital", "…bids from KISUMU  specialist-hospital, Kenya")
    assert not appears("Kisumu Referral", "Kisumu Specialist Hospital")
    assert not appears("ab", "about")  # too short to count as a match
