"""Core domain schemas shared by agents, the orchestrator and the API.

The single most important structural rule of this project is encoded here:

* :class:`AgentVisibleState` is the ONLY state object that may enter a prompt.
* :class:`RuntimeTelemetry`, :class:`ExecutionEvent`, :class:`TokenUsage` are
  observation-only and must never reach a model.

``AgentVisibleState`` deliberately has **no** token/latency/timestamp fields --
there is nothing to leak because the data is not reachable from that object.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.token_usage import TokenUsage, TokenUsageAggregate

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

ActionType = Literal["delegate", "answer", "continue", "review", "replan"]
AgentStatus = Literal["idle", "queued", "running", "thinking", "completed", "failed", "skipped"]
ExecutionStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


# --------------------------------------------------------------------------
# Prompt-facing state  (Context / Agent State)
# --------------------------------------------------------------------------


class AgentVisibleState(BaseModel):
    """Runtime state an LLM legitimately needs in order to decide its next move.

    Injected at the very END of every prompt under an explicit
    ``===== INTERNAL AGENT STATE =====`` banner.

    INVARIANT: no telemetry fields (tokens, latency, cost, timestamps,
    execution ids, UI state) may be added to this model. If a future feature
    genuinely needs a value in the prompt, add it here explicitly so the
    decision stays reviewable in one place.
    """

    user_goal: str = ""
    current_stage: str = "start"
    completed_steps: list[str] = Field(default_factory=list)
    available_agents: list[str] = Field(default_factory=list)
    #: key -> one-line role, for every worker that is actually usable right now.
    #: Rendered as the authoritative catalogue so the MainAgent never has to rely
    #: on a hardcoded list (which would go stale the moment an agent is added,
    #: removed, or taken offline).
    available_workers: dict[str, str] = Field(default_factory=dict)
    important_results: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    next_action_hint: str | None = None
    steps_remaining: int | None = None
    step_index: int | None = None
    local_workers_available: bool = True

    def render(self) -> str:
        """Compact, deterministic text rendering for prompt injection."""
        lines: list[str] = []
        lines.append("===== INTERNAL AGENT STATE =====")
        lines.append(f"user_goal: {self.user_goal or '(unknown)'}")
        lines.append(f"current_stage: {self.current_stage}")
        lines.append(f"available_agents: {', '.join(self.available_agents) or '(none)'}")
        lines.append(f"local_workers_available: {str(self.local_workers_available).lower()}")

        if self.available_workers:
            lines.append("available_workers (delegate only to these):")
            for key, role in self.available_workers.items():
                lines.append(f"  - {key}: {role}")
        else:
            lines.append("available_workers: (none — complete the request yourself)")

        if self.step_index is not None:
            lines.append(f"step_index: {self.step_index}")
        if self.steps_remaining is not None:
            lines.append(f"steps_remaining: {self.steps_remaining}")

        if self.completed_steps:
            lines.append("completed_steps:")
            lines.extend(f"  - {s}" for s in self.completed_steps)
        else:
            lines.append("completed_steps: (none yet)")

        if self.important_results:
            lines.append("important_results:")
            for agent, result in self.important_results.items():
                lines.append(f"  [{agent}]: {result}")
        else:
            lines.append("important_results: (none yet)")

        if self.errors:
            lines.append("errors:")
            lines.extend(f"  - {e}" for e in self.errors)

        if self.next_action_hint:
            lines.append(f"next_action_hint: {self.next_action_hint}")

        return "\n".join(lines)


# --------------------------------------------------------------------------
# Agent protocol
# --------------------------------------------------------------------------


class AgentTask(BaseModel):
    """A unit of work handed to a worker agent.

    ``context`` carries only what the worker needs -- the orchestrator never
    forwards the whole conversation history.
    """

    task_id: str
    agent: str
    instruction: str
    context: str = ""
    expected_format: Literal["text", "json"] = "text"
    schema_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    """The MainAgent's structured plan for one step.

    Parsing is fault-tolerant (see :mod:`orchestration.decision_parser`); a
    malformed model response degrades to an ``answer`` decision instead of
    raising.
    """

    action: ActionType = "answer"
    agent: str | None = None
    task: str | None = None
    context: str | None = None
    reason: str | None = None
    answer: str | None = None
    expected_format: Literal["text", "json"] = "text"
    parallel: bool = False
    raw: str | None = Field(default=None, exclude=True)

    def summary_line(self) -> str:
        if self.action == "delegate":
            return f"delegate -> {self.agent}: {(self.task or '')[:120]}"
        if self.action == "review":
            return f"review -> {self.agent or 'local_reviewer'}"
        if self.action == "replan":
            return f"replan: {(self.reason or '')[:120]}"
        if self.action == "continue":
            return f"continue: {(self.task or '')[:120]}"
        return "answer"


class AgentResult(BaseModel):
    """Outcome of a single agent invocation."""

    task_id: str
    agent: str
    status: Literal["completed", "failed", "skipped"] = "completed"
    output: str = ""
    parsed: dict[str, Any] | None = None
    reasoning: str | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


# --------------------------------------------------------------------------
# User-visible telemetry  (NEVER goes into a prompt)
# --------------------------------------------------------------------------


class RuntimeTelemetry(BaseModel):
    """Live, human-facing view of one execution.

    Explicitly excluded from every prompt construction path. ``prompt_builder``
    accepts no argument of this type, so misuse is a type error.
    """

    execution_id: str
    active_agent: str | None = None
    active_agent_label: str | None = None
    active_model: str | None = None
    active_is_local: bool = False
    status: ExecutionStatus = "pending"
    stage: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: float | None = None
    tokens_per_second: float | None = None
    step_count: int = 0
    updated_at: datetime | None = None


# --------------------------------------------------------------------------
# Execution records
# --------------------------------------------------------------------------


class AgentStep(BaseModel):
    """One row of the execution timeline."""

    id: int | None = None
    execution_id: str
    step_index: int
    agent_name: str
    agent_label: str | None = None
    model: str | None = None
    is_local: bool = False
    task: str = ""
    status: AgentStatus = "queued"
    stage: str = ""
    input: str | None = None
    output: str | None = None
    reasoning: str | None = None
    parsed_output: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    usage: TokenUsage | None = None
    # Debug-only extras (raw model text, parse errors, retries)
    debug: dict[str, Any] | None = None


class AgentFlowNode(BaseModel):
    """A node in the compact Agent Flow diagram."""

    index: int
    agent_name: str
    agent_label: str
    model: str | None = None
    is_local: bool = False
    status: AgentStatus = "queued"
    stage: str = ""


class Execution(BaseModel):
    id: str
    conversation_id: str
    user_message_id: int | None = None
    assistant_message_id: int | None = None
    status: ExecutionStatus = "pending"
    user_input: str = ""
    final_answer: str | None = None
    error: str | None = None
    reasoning: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    step_count: int = 0
    flow: list[AgentFlowNode] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    usage: TokenUsage | None = None


class Conversation(BaseModel):
    id: str
    title: str = "New conversation"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    message_count: int = 0


class Message(BaseModel):
    id: int | None = None
    conversation_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    reasoning: str | None = None
    execution_id: str | None = None
    created_at: datetime | None = None
    usage: TokenUsage | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None
    # Optional per-request budget override (clamped server-side).
    max_steps: int | None = Field(default=None, ge=1, le=64)


class ChatResponse(BaseModel):
    """Immediate acknowledgement; the work itself proceeds in the background."""

    execution_id: str
    conversation_id: str
    user_message_id: int
    status: ExecutionStatus = "running"
    stream_url: str


class AgentDescriptor(BaseModel):
    """Static description of a registered agent, for the Agent panel."""

    key: str
    label: str
    model: str
    provider: str
    is_local: bool
    role: str
    reasoning_effort: str = "none"
    available: bool = True


class ModelStatus(BaseModel):
    name: str
    provider: str
    is_local: bool
    status: Literal["online", "offline", "configured", "unknown", "error"]
    detail: str | None = None
    latency_ms: float | None = None
    models: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    backend: str = "online"
    deepseek: str = "unknown"
    minicpm: str = "unknown"
    local_workers_enabled: bool = True
    database: str = "unknown"
    version: str = "1.0.0"
    details: dict[str, Any] = Field(default_factory=dict)


class StatsResponse(BaseModel):
    requests: int = 0
    agent_calls: int = 0
    deepseek_calls: int = 0
    local_calls: int = 0
    total_tokens: int | None = None
    local_tokens: int | None = None
    cloud_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    average_latency_ms: float | None = None
    cost_usd: float | None = None
    by_agent: dict[str, TokenUsageAggregate] = Field(default_factory=dict)


__all__ = [
    "ActionType",
    "AgentDecision",
    "AgentDescriptor",
    "AgentFlowNode",
    "AgentResult",
    "AgentStatus",
    "AgentStep",
    "AgentTask",
    "AgentVisibleState",
    "ChatRequest",
    "ChatResponse",
    "Conversation",
    "Execution",
    "ExecutionStatus",
    "HealthResponse",
    "Message",
    "ModelStatus",
    "RuntimeTelemetry",
    "StatsResponse",
]
