"""SSE event schema.

One flat envelope keeps the wire format trivial for the browser to consume::

    {"type": "agent_started", "execution_id": "...", "ts": "...", ...}

Events are the transport for :class:`models.schemas.RuntimeTelemetry`. They are
never rendered into a prompt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.token_usage import TokenUsage, utcnow

EventType = Literal[
    "execution_started",
    "execution_completed",
    "execution_failed",
    "execution_cancelled",
    "agent_queued",
    "agent_started",
    "agent_reasoning",
    "agent_output",
    "agent_completed",
    "agent_failed",
    "decision",
    "token_usage",
    "telemetry",
    "flow_update",
    "final_answer",
    "final_answer_delta",
    "notice",
    "debug",
    "heartbeat",
]


class ExecutionEvent(BaseModel):
    """A single SSE frame."""

    type: EventType
    execution_id: str | None = None
    ts: datetime = Field(default_factory=utcnow)

    # Attribution
    agent: str | None = None
    agent_label: str | None = None
    model: str | None = None
    provider: str | None = None
    is_local: bool | None = None

    # Payloads (only the relevant ones are populated per event type)
    stage: str | None = None
    status: str | None = None
    step_index: int | None = None
    text: str | None = None
    delta: str | None = None
    message: str | None = None
    level: Literal["info", "warning", "error"] = "info"
    usage: TokenUsage | None = None
    data: dict[str, Any] | None = None

    def to_sse(self) -> dict[str, str]:
        """Serialize to the ``{event, data}`` shape consumed by EventSource."""
        return {
            "event": self.type,
            "data": self.model_dump_json(exclude_none=True),
        }


__all__ = ["EventType", "ExecutionEvent"]
