"""Provider-neutral protocols. Nothing outside these boundaries knows about vendors."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.types import (
    ActionKind,
    Decision,
    EvidenceKind,
    ModelTier,
    RiskLevel,
    VerificationStatus,
)


class ModelRequest(BaseModel):
    task_type: str
    prompt: str
    system: str | None = None
    tier: ModelTier = ModelTier.BULK
    max_output_tokens: int = 1200
    complexity: float = Field(default=0.5, ge=0.0, le=1.0)
    expects_json: bool = True
    context: dict[str, Any] = Field(default_factory=dict)


class ModelResponse(BaseModel):
    text: str
    provider: str
    model: str
    tier: ModelTier
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    cost_usd: float = 0.0
    source: str | None = None


class EvidenceRecord(BaseModel):
    claim: str
    kind: EvidenceKind
    source: str
    source_type: str = "model"
    retrieved_at: datetime | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    subject_type: str | None = None
    subject_id: str | None = None


class ActionRequest(BaseModel):
    kind: ActionKind
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    cost_category: str = "ai_primary"
    risk: RiskLevel = RiskLevel.LOW
    idempotency_key: str | None = None
    objective_id: str | None = None
    opportunity_id: str | None = None


class PolicyResult(BaseModel):
    decision: Decision
    reasons: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW


class AgentResult(BaseModel):
    agent: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    cost_usd: float = 0.0
    notes: list[str] = Field(default_factory=list)
    next_tasks: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def supports(self, tier: ModelTier) -> bool: ...

    def estimate_cost(self, request: ModelRequest) -> float: ...

    def complete(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class ModelRouterProtocol(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class Tool(Protocol):
    name: str

    def run(self, **kwargs: Any) -> ToolResult: ...


@runtime_checkable
class Agent(Protocol):
    name: str

    def run(self, ctx: Any, task_input: dict[str, Any]) -> AgentResult: ...


@runtime_checkable
class MemoryStore(Protocol):
    def upsert_company(self, **kwargs: Any) -> Any: ...
    def record_evidence(self, record: EvidenceRecord) -> Any: ...


@runtime_checkable
class TaskStore(Protocol):
    def create_task(self, **kwargs: Any) -> Any: ...
    def next_pending(self, objective_id: str) -> Any: ...


@runtime_checkable
class PolicyEngineProtocol(Protocol):
    def evaluate(self, request: ActionRequest, ctx: Any) -> PolicyResult: ...


@runtime_checkable
class BudgetControllerProtocol(Protocol):
    def can_spend(self, category: str, amount: float) -> bool: ...
    def reserve(self, category: str, amount: float, reason: str) -> str: ...
    def commit(self, reservation_id: str, actual: float) -> None: ...


@runtime_checkable
class SchedulerProtocol(Protocol):
    def run_due(self, now: datetime) -> list[str]: ...


@runtime_checkable
class Evaluator(Protocol):
    def evaluate(self, window_days: int) -> dict[str, Any]: ...


@runtime_checkable
class AuditLogger(Protocol):
    def record(self, event_type: str, **fields: Any) -> None: ...
