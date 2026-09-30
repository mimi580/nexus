"""Append-only audit trail. Every decision, block and spend lands here."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event, redact
from app.database.models import AuditEvent

logger = get_logger("nexus.audit")


class DbAuditLogger:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        event_type: str,
        *,
        summary: str = "",
        actor: str = "nexus",
        objective_id: str | None = None,
        task_id: str | None = None,
        decision: str | None = None,
        **payload: Any,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            actor=actor,
            objective_id=objective_id,
            task_id=task_id,
            decision=decision,
            summary=redact(summary)[:4000],
            payload=payload,
        )
        self.session.add(event)
        self.session.flush()
        if decision == "escalate":
            self._open_review(event_type, summary, objective_id, task_id, payload)
        log_event(
            logger,
            logging.INFO,
            event_type,
            audit_id=event.id,
            decision=decision,
            objective_id=objective_id,
            task_id=task_id,
            summary=summary,
        )
        return event

    def _open_review(
        self,
        event_type: str,
        summary: str,
        objective_id: str | None,
        task_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        from app.review.queue import open_item

        open_item(
            self.session,
            event_type=event_type,
            title=summary,
            reasons=list(payload.get("reasons") or []),
            rule_ids=list(payload.get("rule_ids") or []),
            review_key=payload.get("review_key"),
            opportunity_id=payload.get("opportunity_id"),
            objective_id=objective_id,
            task_id=task_id,
            action_kind=payload.get("action_kind"),
            action_payload=payload.get("action_preview") or {},
        )
