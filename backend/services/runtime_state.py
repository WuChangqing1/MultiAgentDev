"""User-visible runtime state.

Maintains the live :class:`RuntimeTelemetry` for each in-flight execution and
tracks per-agent status for the Agent panel.

IMPORTANT: this module exists for the UI only. ``RuntimeTelemetry`` is never
routed into a prompt -- the orchestrator builds prompts exclusively from
``Context`` + ``AgentVisibleState``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from models.events import ExecutionEvent
from models.schemas import AgentStatus, ExecutionStatus, RuntimeTelemetry
from models.token_usage import utcnow
from services.event_bus import EventBus

#: Agents shown in the left-hand panel, in display order.
AGENT_DISPLAY_ORDER = [
    "main",
    "local_extractor",
    "local_summarizer",
    "local_classifier",
    "local_reviewer",
]


class RuntimeStateStore:
    """Live status of every execution plus the current agent status map."""

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._telemetry: dict[str, RuntimeTelemetry] = {}
        self._agent_status: dict[str, AgentStatus] = {key: "idle" for key in AGENT_DISPLAY_ORDER}
        self._current_execution: str | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------
    async def begin(self, execution_id: str, goal: str) -> RuntimeTelemetry:
        telemetry = RuntimeTelemetry(
            execution_id=execution_id,
            status="running",
            stage="planning",
            updated_at=utcnow(),
        )
        async with self._lock:
            self._telemetry[execution_id] = telemetry
            self._current_execution = execution_id
        await self._emit(telemetry)
        return telemetry

    async def finish(
        self,
        execution_id: str,
        *,
        status: ExecutionStatus,
        stage: str = "done",
        error: str | None = None,
    ) -> RuntimeTelemetry | None:
        async with self._lock:
            telemetry = self._telemetry.get(execution_id)
            if telemetry is None:
                return None
            telemetry.status = status
            telemetry.stage = stage
            telemetry.active_agent = None
            telemetry.updated_at = utcnow()
        await self._emit(telemetry)
        if error:
            await self.notice(execution_id, error, level="error")
        return telemetry

    # -- mutations ---------------------------------------------------------
    async def set_active_agent(
        self,
        execution_id: str,
        *,
        agent_key: str,
        agent_label: str,
        model: str,
        is_local: bool,
        stage: str,
        status: AgentStatus = "running",
    ) -> None:
        async with self._lock:
            telemetry = self._telemetry.get(execution_id)
            if telemetry is not None:
                # Relabel the previous agent as completed when switching.
                if telemetry.active_agent and telemetry.active_agent != agent_key:
                    key = telemetry.active_agent
                    if self._agent_status.get(key) == "running":
                        self._agent_status[key] = "completed"
                telemetry.active_agent = agent_key
                telemetry.active_agent_label = agent_label
                telemetry.active_model = model
                telemetry.active_is_local = is_local
                telemetry.stage = stage
                telemetry.updated_at = utcnow()
            self._agent_status[agent_key] = status
        await self._emit(self._telemetry.get(execution_id))

    async def set_agent_status(self, agent_key: str, status: AgentStatus) -> None:
        async with self._lock:
            self._agent_status[agent_key] = status

    async def bump_step(self, execution_id: str, step_count: int) -> None:
        async with self._lock:
            telemetry = self._telemetry.get(execution_id)
            if telemetry is not None:
                telemetry.step_count = step_count
                telemetry.updated_at = utcnow()

    async def apply_usage(self, execution_id: str, totals) -> None:
        """Copy aggregate token figures into the live telemetry."""
        async with self._lock:
            telemetry = self._telemetry.get(execution_id)
            if telemetry is None:
                return
            telemetry.prompt_tokens = totals.prompt_tokens
            telemetry.completion_tokens = totals.completion_tokens
            telemetry.reasoning_tokens = totals.reasoning_tokens
            telemetry.total_tokens = totals.total_tokens
            telemetry.latency_ms = totals.latency_ms
            telemetry.tokens_per_second = totals.tokens_per_second
            telemetry.updated_at = utcnow()
        await self._emit(self._telemetry.get(execution_id))

    # -- reads -------------------------------------------------------------
    def get(self, execution_id: str) -> RuntimeTelemetry | None:
        return self._telemetry.get(execution_id)

    def agent_statuses(self) -> dict[str, AgentStatus]:
        return dict(self._agent_status)

    def current_execution(self) -> str | None:
        return self._current_execution

    async def reset_agents(self) -> None:
        async with self._lock:
            for key in self._agent_status:
                self._agent_status[key] = "idle"

    async def mark_idle_agents(self) -> None:
        """Queue-time reset: nothing is running before a request starts."""
        async with self._lock:
            for key in self._agent_status:
                self._agent_status[key] = "idle"

    # -- helpers -----------------------------------------------------------
    async def notice(self, execution_id: str, message: str, *, level: str = "info") -> None:
        self._bus.publish(
            ExecutionEvent(
                type="notice",
                execution_id=execution_id,
                message=message,
                level=level,  # type: ignore[arg-type]
            )
        )

    async def _emit(self, telemetry: RuntimeTelemetry | None) -> None:
        if telemetry is None:
            return
        self._bus.publish(
            ExecutionEvent(
                type="telemetry",
                execution_id=telemetry.execution_id,
                agent=telemetry.active_agent,
                agent_label=telemetry.active_agent_label,
                model=telemetry.active_model,
                is_local=telemetry.active_is_local,
                status=telemetry.status,
                stage=telemetry.stage,
                data={
                    "telemetry": telemetry.model_dump(mode="json"),
                    "agent_status": {k: str(v) for k, v in self._agent_status.items()},
                },
            )
        )


__all__ = ["AGENT_DISPLAY_ORDER", "RuntimeStateStore"]
