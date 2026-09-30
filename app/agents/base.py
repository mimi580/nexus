"""Agent base class: model access, evidence recording, structured results."""

from __future__ import annotations

from typing import Any

from app.core.context import RunContext
from app.core.errors import MalformedModelOutput
from app.core.interfaces import AgentResult, EvidenceRecord, ModelRequest
from app.core.types import EvidenceKind, ModelTier, VerificationStatus

SYSTEM_PROMPT = (
    "You are a component of NEXUS, a commercial-intelligence platform. "
    "Return only a single JSON object matching the requested schema. "
    "Never invent people, companies, prices, stock, certifications, approvals or references. "
    "If something is unknown, use null and say so."
)


class BaseAgent:
    name: str = "agent"
    task_type: str = "generic"
    tier: ModelTier = ModelTier.BULK
    complexity: float = 0.5

    def run(self, ctx: RunContext, task_input: dict[str, Any]) -> AgentResult:
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers
    def ask(
        self,
        ctx: RunContext,
        prompt: str,
        context: dict[str, Any],
        *,
        task_type: str | None = None,
        tier: ModelTier | None = None,
        complexity: float | None = None,
        retries: int = 1,
    ) -> tuple[dict[str, Any], float]:
        from app.models.providers.base import parse_json

        request = ModelRequest(
            task_type=task_type or self.task_type,
            prompt=prompt,
            system=SYSTEM_PROMPT,
            tier=tier or self.tier,
            complexity=self.complexity if complexity is None else complexity,
            context=context,
        )
        cost = 0.0
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            response = ctx.router.complete(request)
            cost += response.cost_usd
            try:
                return parse_json(response), cost
            except MalformedModelOutput as exc:
                last_error = exc
                ctx.audit.record(
                    "malformed_model_output",
                    summary=f"{request.task_type} attempt {attempt + 1}",
                    task_id=ctx.task_id,
                    provider=response.provider,
                )
                request = request.model_copy(
                    update={"prompt": prompt + "\n\nReturn valid JSON only.", "tier": ModelTier.CRITIC}
                )
        assert last_error is not None
        raise last_error

    @staticmethod
    def evidence(
        claim: str,
        source: str,
        *,
        kind: EvidenceKind = EvidenceKind.MODEL_HYPOTHESIS,
        confidence: float = 0.4,
        subject_type: str | None = None,
        subject_id: str | None = None,
        source_type: str = "model",
        verification: VerificationStatus = VerificationStatus.UNVERIFIED,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            claim=claim,
            kind=kind,
            source=source,
            source_type=source_type,
            confidence=confidence,
            verification=verification,
            subject_type=subject_type,
            subject_id=subject_id,
        )

    def persist_evidence(self, ctx: RunContext, records: list[EvidenceRecord]) -> None:
        for record in records:
            ctx.memory.record_evidence(record)

    def ok(self, **kwargs: Any) -> AgentResult:
        return AgentResult(agent=self.name, ok=True, **kwargs)

    def fail(self, error: str, **kwargs: Any) -> AgentResult:
        return AgentResult(agent=self.name, ok=False, error=error, **kwargs)
