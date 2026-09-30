"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

REDACTION = "***REDACTED***"

# Patterns where the whole match is the secret.
WHOLE_MATCH_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
]

# Patterns where group 1 is a harmless prefix and group 2 is the secret.
PREFIXED_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)([^\s\"\',]+)"),
    re.compile(r"(?i)(password\"?\s*[:=]\s*\"?)([^\s\"\',]+)"),
    re.compile(r"(?i)(secret\"?\s*[:=]\s*\"?)([^\s\"\',]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-._~+/]+=*)"),
]


def redact(text: str) -> str:
    out = text
    for pattern in WHOLE_MATCH_PATTERNS:
        out = pattern.sub(REDACTION, out)
    for pattern in PREFIXED_PATTERNS:
        out = pattern.sub(lambda m: m.group(1) + REDACTION, out)
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)  # stdout is reserved for command output
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"extra_fields": fields})
