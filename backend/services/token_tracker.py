"""Token accounting service.

Single funnel through which every model call's usage passes. Responsibilities:

1. Bind the ambient execution/agent/step identity onto a :class:`TokenUsage`.
2. Persist one ``model_calls`` row per invocation (telemetry of record).
3. Keep a live per-execution roll-up so the UI can render "Current Request"
   without a round trip to the database.
4. Broadcast the usage as an SSE event.

Nothing here is ever passed to :func:`core.prompt_builder.build_agent_prompt`.
"""

from __future__ import annotations

import asyncio
import logging

from db.database import Database
from models.events import ExecutionEvent
from models.token_usage import TokenUsage, TokenUsageAggregate
from services.event_bus import EventBus

log = logging.getLogger(__name__)


class TokenTracker:
    """Records usage and maintains live aggregates."""

    def __init__(self, database: Database, event_bus: EventBus) -> None:
        self._db = database
        self._bus = event_bus
        self._live: dict[str, list[TokenUsage]] = {}
        self._lock = asyncio.Lock()

    # -- recording ---------------------------------------------------------
    async def record(
        self,
        usage: TokenUsage,
        *,
        execution_id: str | None,
        agent_name: str,
        agent_step_id: int | None = None,
    ) -> TokenUsage:
        """Persist and broadcast one model call's usage."""
        usage.agent_name = agent_name
        usage.finalize()

        if execution_id:
            async with self._lock:
                self._live.setdefault(execution_id, []).append(usage)

        try:
            await self._db.add_model_call(
                execution_id=execution_id,
                agent_step_id=agent_step_id,
                agent_name=agent_name,
                usage=usage,
            )
        except Exception:  # noqa: BLE001 - telemetry must never break a request
            log.warning("Failed to persist model call telemetry", exc_info=True)

        if execution_id:
            aggregate = self.aggregate(execution_id)
            self._bus.publish(
                ExecutionEvent(
                    type="token_usage",
                    execution_id=execution_id,
                    agent=agent_name,
                    model=usage.model,
                    provider=usage.provider,
                    is_local=usage.is_local,
                    usage=usage,
                    data={
                        "execution_totals": aggregate.model_dump(mode="json"),
                        "reasoning_estimated": usage.reasoning_tokens_source == "estimated",
                    },
                )
            )
        return usage

    # -- reads -------------------------------------------------------------
    def aggregate(self, execution_id: str) -> TokenUsageAggregate:
        return TokenUsageAggregate.from_usages(self._live.get(execution_id, []))

    def by_agent(self, execution_id: str) -> dict[str, TokenUsageAggregate]:
        buckets: dict[str, list[TokenUsage]] = {}
        for usage in self._live.get(execution_id, []):
            buckets.setdefault(usage.agent_name or "unknown", []).append(usage)
        return {name: TokenUsageAggregate.from_usages(items) for name, items in buckets.items()}

    def usages(self, execution_id: str) -> list[TokenUsage]:
        return list(self._live.get(execution_id, []))

    def forget(self, execution_id: str) -> None:
        self._live.pop(execution_id, None)


__all__ = ["TokenTracker"]
