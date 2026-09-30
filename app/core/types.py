"""Shared enums and value objects."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Naive UTC. Every stored timestamp is UTC; naive keeps SQLite and
    PostgreSQL comparisons identical."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class ObjectiveStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class OpportunityStage(StrEnum):
    DISCOVERED = "discovered"
    RESEARCHED = "researched"
    QUALIFIED = "qualified"
    SCORED = "scored"
    TARGETED = "targeted"
    OUTREACH = "outreach"
    RESPONSE = "response"
    FOLLOW_UP = "follow_up"
    NEGOTIATION = "negotiation"
    COMMERCIAL_REVIEW = "commercial_review"
    WON = "won"
    LOST = "lost"
    STALE = "stale"


TERMINAL_STAGES = {OpportunityStage.WON, OpportunityStage.LOST, OpportunityStage.STALE}


class ProductCategory(StrEnum):
    LAPTOP = "refurbished_laptop"
    IPHONE = "used_iphone"
    MEDICAL = "medical_equipment"
    PHARMA = "pharmaceutical"
    SERVER_IT = "server_it"


REGULATED_CATEGORIES = {ProductCategory.MEDICAL, ProductCategory.PHARMA}


class EvidenceKind(StrEnum):
    VERIFIED_FACT = "verified_fact"
    UNVERIFIED_CLAIM = "unverified_claim"
    INFERENCE = "inference"
    USER_PROVIDED = "user_provided"
    MODEL_HYPOTHESIS = "model_hypothesis"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    REFUTED = "refuted"


class ResponseCategory(StrEnum):
    INTERESTED = "interested"
    INFORMATION_REQUEST = "information_request"
    PRICE_REQUEST = "price_request"
    RFQ = "rfq"
    NEGOTIATION = "negotiation"
    NOT_INTERESTED = "not_interested"
    WRONG_CONTACT = "wrong_contact"
    UNSUBSCRIBE = "unsubscribe"
    COMPLAINT = "complaint"
    SUSPICIOUS = "suspicious"
    REGULATORY_ISSUE = "regulatory_issue"
    OTHER = "other"


STOP_FOLLOWUP_CATEGORIES = {
    ResponseCategory.UNSUBSCRIBE,
    ResponseCategory.COMPLAINT,
    ResponseCategory.NOT_INTERESTED,
    ResponseCategory.SUSPICIOUS,
}


class ActionKind(StrEnum):
    RESEARCH = "research"
    MODEL_CALL = "model_call"
    SEND_OUTREACH = "send_outreach"
    SEND_FOLLOWUP = "send_followup"
    SEND_REPLY = "send_reply"  # answering a buyer: quotes, RFQ responses, information
    CRM_WRITE = "crm_write"
    FINANCIAL_COMMITMENT = "financial_commitment"
    LEGAL_COMMITMENT = "legal_commitment"
    REGULATED_TRANSACTION = "regulated_transaction"
    DESTRUCTIVE = "destructive"
    EXTERNAL_PURCHASE = "external_purchase"


class Decision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelTier(StrEnum):
    REASONING = "reasoning"
    BULK = "bulk"
    CRITIC = "critic"


class Money(BaseModel):
    """A commercial amount that may be a range. Never silently invented."""

    low: float
    high: float
    currency: str = "USD"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    basis: str = "unknown"

    @classmethod
    def exact(cls, value: float, currency: str = "USD", basis: str = "given") -> "Money":
        return cls(low=value, high=value, currency=currency, confidence=1.0, basis=basis)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def is_range(self) -> bool:
        return self.high > self.low

    def __add__(self, other: "Money") -> "Money":
        if other.currency != self.currency:
            raise ValueError("currency mismatch")
        return Money(
            low=self.low + other.low,
            high=self.high + other.high,
            currency=self.currency,
            confidence=min(self.confidence, other.confidence),
            basis="sum",
        )
