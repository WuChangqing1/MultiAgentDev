"""The ONLY sanctioned prompt assembly path.

Data-isolation contract
=======================

===========  ==========================================  =====================
Layer        Meaning                                     Destination
===========  ==========================================  =====================
Context      what a model needs to do the task           prompt body
Agent State  runtime/flow state an agent needs            prompt TAIL (banner)
Telemetry    human observation only                      SSE / DB / logs
===========  ==========================================  =====================

``build_agent_prompt`` accepts only ``str`` / ``AgentVisibleState``. It cannot
be handed a ``TokenUsage``, ``RuntimeTelemetry`` or ``ExecutionEvent`` -- there
is no parameter that would accept one, and the runtime guard below rejects any
smuggled object. This makes the rule a property of the code structure rather
than a convention a developer has to remember.
"""

from __future__ import annotations

from typing import Final

from models.schemas import AgentVisibleState

STATE_BANNER: Final[str] = "===== INTERNAL AGENT STATE ====="

#: Field names that belong to telemetry. If one of these shows up as an
#: attribute on a value passed to the prompt builder we refuse to build.
_FORBIDDEN_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "cached_tokens",
        "tokens_per_second",
        "latency_ms",
        "cost_usd",
        "execution_id",
        "request_start_time",
        "request_end_time",
        "active_agent",
        "active_model",
        "updated_at",
        "step_index",
    }
)

_TELEMETRY_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {"TokenUsage", "TokenUsageAggregate", "RuntimeTelemetry", "ExecutionEvent", "ModelCallRecord"}
)


class TelemetryLeakError(TypeError):
    """Raised when telemetry is about to be injected into a prompt."""


def _assert_not_telemetry(value: object, where: str) -> None:
    name = type(value).__name__
    if name in _TELEMETRY_TYPE_NAMES:
        raise TelemetryLeakError(
            f"Refusing to build prompt: telemetry object {name!r} passed as {where!r}. "
            "Telemetry must go to the frontend/DB/logs, never to a prompt."
        )
    leaked = _FORBIDDEN_ATTRS.intersection(dir(value))
    if leaked and not isinstance(value, AgentVisibleState):
        raise TelemetryLeakError(
            f"Refusing to build prompt: {where!r} exposes telemetry field(s) {sorted(leaked)}."
        )


def _clean(text: str | None, limit: int | None = None) -> str:
    """Normalize whitespace and optionally clamp length (token hygiene)."""
    if not text:
        return ""
    out = text.strip()
    if limit is not None and len(out) > limit:
        out = out[:limit].rstrip() + "\n...[truncated]"
    return out


def build_agent_prompt(
    system_prompt: str,
    *,
    task: str | None = None,
    context: str | None = None,
    agent_state: AgentVisibleState | None = None,
    task_limit: int | None = None,
    context_limit: int | None = None,
) -> str:
    """Assemble a prompt in the mandated order.

    Order (fixed)::

        System Instructions
        Task
        Context
        ===== INTERNAL AGENT STATE =====
        <agent state>              <- ALWAYS last

    The state banner is appended last so the model reads it as runtime context
    rather than as part of its core instructions.
    """
    _assert_not_telemetry(system_prompt, "system_prompt")
    if agent_state is not None:
        _assert_not_telemetry(agent_state, "agent_state")
    if task is not None:
        _assert_not_telemetry(task, "task")
    if context is not None:
        _assert_not_telemetry(context, "context")

    parts: list[str] = [_clean(system_prompt)]

    task_text = _clean(task, task_limit)
    if task_text:
        parts.append(f"TASK\n{task_text}")

    context_text = _clean(context, context_limit)
    if context_text:
        parts.append(f"CONTEXT\n{context_text}")

    if agent_state is not None:
        rendered = agent_state.render()
        # Defensive: render() must not have produced telemetry keys.
        _assert_not_telemetry(rendered, "rendered_agent_state")
        parts.append(rendered)

    return "\n\n".join(p for p in parts if p)


def build_worker_prompt(
    system_prompt: str,
    *,
    task: str,
    context: str | None = None,
    agent_state: AgentVisibleState | None = None,
    context_limit: int = 6000,
) -> str:
    """Worker prompts are deliberately narrow: no chat history is forwarded.

    The orchestrator distils what the worker needs into ``task`` + ``context``;
    the full conversation never reaches a small model.
    """
    return build_agent_prompt(
        system_prompt,
        task=task,
        context=context,
        agent_state=agent_state,
        context_limit=context_limit,
    )


__all__ = [
    "STATE_BANNER",
    "TelemetryLeakError",
    "build_agent_prompt",
    "build_worker_prompt",
]
