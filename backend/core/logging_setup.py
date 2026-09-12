"""Structured logging.

Emits one line per event with a stable key=value tail so logs stay greppable::

    2025-01-01 12:00:00 INFO  multiagent [exec=abc123 agent=main] agent_started status=running duration_ms=1234

Redaction rules:
* API keys / tokens are never logged (see :func:`core.config.redact`).
* User content is truncated hard; full prompts live in SQLite (local) only.
"""

from __future__ import annotations

import contextvars
import logging
import sys
from typing import Any

_CONFIGURED = False

#: Ambient execution id, so every log line inside a request is attributable.
execution_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("execution_id", default=None)

_SECRET_HINTS = ("api_key", "apikey", "authorization", "secret", "token_key", "password")
_MAX_VALUE_LEN = 200


class _StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} {record.levelname:<5} multiagent"
        exec_id = getattr(record, "execution_id", None) or execution_id_var.get()
        if exec_id:
            base += f" [exec={exec_id[:8]}]"
        agent = getattr(record, "agent", None)
        if agent:
            base += f" [agent={agent}]"
        message = record.getMessage()
        tail = _format_extras(record)
        line = f"{base} {message}{tail}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _format_extras(record: logging.LogRecord) -> str:
    extras = {
        k: v
        for k, v in record.__dict__.items()
        if k
        not in {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
            "execution_id",
            "agent",
            "message",
            "asctime",
        }
        and not k.startswith("_")
    }
    if not extras:
        return ""
    rendered = " ".join(f"{k}={_safe(v, k)}" for k, v in extras.items())
    return f" {rendered}"


def _safe(value: Any, key: str = "") -> str:
    lowered = key.lower()
    if any(hint in lowered for hint in _SECRET_HINTS):
        return "<redacted>"
    text = str(value).replace("\n", "\\n").replace("\r", "")
    if len(text) > _MAX_VALUE_LEN:
        text = text[:_MAX_VALUE_LEN] + "..."
    return text


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # These are chatty and never add signal here.
    for noisy in ("httpx", "httpcore", "openai", "uvicorn.access", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


__all__ = ["configure_logging", "execution_id_var", "get_logger"]
