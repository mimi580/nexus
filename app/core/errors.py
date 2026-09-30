"""Structured error types. Every failure the platform can recover from is typed."""


class NexusError(Exception):
    """Base class for all NEXUS errors."""

    code = "nexus_error"

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "context": self.context}


class ConfigError(NexusError):
    code = "config_error"


class ProviderError(NexusError):
    """A model/tool provider failed. Retryable or fallback-able."""

    code = "provider_error"


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


class MalformedModelOutput(ProviderError):
    code = "malformed_model_output"


class BudgetExceeded(NexusError):
    code = "budget_exceeded"


class PolicyViolation(NexusError):
    code = "policy_violation"


class EscalationRequired(NexusError):
    code = "escalation_required"


class UnknownInput(NexusError):
    """A required commercial input is unknown. Never guessed."""

    code = "unknown_input"


class LoopAborted(NexusError):
    code = "loop_aborted"


class DuplicateAction(NexusError):
    code = "duplicate_action"
