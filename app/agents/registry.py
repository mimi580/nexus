from __future__ import annotations

from app.ads.agents import AdLaunchAgent, AdOptimizerAgent, AdPlannerAgent, ConversionUploadAgent
from app.agents.base import BaseAgent
from app.agents.learning import LearningAgent
from app.agents.market_research import MarketResearchAgent
from app.agents.outreach import FollowUpAgent, OutreachAgent
from app.agents.prospecting import (
    CompanyIntelligenceAgent,
    DecisionMakerAgent,
    ProspectDiscoveryAgent,
)
from app.agents.qualification import OpportunityScoringAgent, QualificationAgent
from app.agents.reply import ReplyAgent
from app.agents.suppliers import SupplierProfileAgent, SupplierResearchAgent, SupplierRFQAgent
from app.agents.response import ResponseAgent
from app.agents.sourcing import SalesStrategyAgent, SourcingAgent
from app.site.leads import InboundLeadAgent, InboundQuoteAgent
from app.site.pages import LandingPageAgent


def build_registry(scoring_threshold: float = 0.45) -> dict[str, BaseAgent]:
    agents: list[BaseAgent] = [
        MarketResearchAgent(),
        ProspectDiscoveryAgent(),
        CompanyIntelligenceAgent(),
        DecisionMakerAgent(),
        QualificationAgent(),
        OpportunityScoringAgent(threshold=scoring_threshold),
        SourcingAgent(),
        SalesStrategyAgent(),
        OutreachAgent(),
        FollowUpAgent(),
        ResponseAgent(),
        ReplyAgent(),
        SupplierResearchAgent(),
        SupplierProfileAgent(),
        SupplierRFQAgent(),
        LearningAgent(),
        LandingPageAgent(),
        InboundLeadAgent(),
        InboundQuoteAgent(),
        AdPlannerAgent(),
        AdLaunchAgent(),
        AdOptimizerAgent(),
        ConversionUploadAgent(),
    ]
    return {agent.name: agent for agent in agents}


AGENT_NAMES = tuple(build_registry().keys())
