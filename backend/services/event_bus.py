"""In-process pub/sub for execution events (SSE transport).

Design:

* One :class:`ExecutionChannel` per execution, holding a replay backlog plus
  live subscriber queues.
* Late subscribers (the common case -- the browser connects a moment after
  ``POST /api/chat``) immediately receive the backlog, so no event is ever
  missed and the UI never starts blank.
* Publishing to a full queue drops the oldest frame for that subscriber rather
  than blocking the orchestrator: telemetry must never stall inference.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from models.events import ExecutionEvent

log = logging.getLogger(__name__)

_BACKLOG_LIMIT = 2000
_QUEUE_LIMIT = 1000
_TERMINAL_TYPES = {"execution_completed", "execution_failed", "execution_cancelled"}


class ExecutionChannel:
    """Replayable event stream for a single execution."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self._backlog: deque[ExecutionEvent] = deque(maxlen=_BACKLOG_LIMIT)
        self._subscribers: set[asyncio.Queue[ExecutionEvent | None]] = set()
        self._closed = False

    # -- publish -----------------------------------------------------------
    def publish(self, event: ExecutionEvent) -> None:
        if self._closed:
            return
        self._backlog.append(event)
        if event.type in _TERMINAL_TYPES:
            self._closed = True

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop its oldest frame and keep the newest.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass

        if event.type in _TERMINAL_TYPES:
            self._signal_end()

    def _signal_end(self) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    # -- subscribe ---------------------------------------------------------
    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[AsyncIterator[ExecutionEvent]]:
        queue: asyncio.Queue[ExecutionEvent | None] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        # Replay first so the browser always sees execution_started -> ... .
        for event in list(self._backlog):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover
                break
        if any(e.type in _TERMINAL_TYPES for e in self._backlog):
            await queue.put(None)

        self._subscribers.add(queue)
        try:
            yield self._iterate(queue)
        finally:
            self._subscribers.discard(queue)

    async def _iterate(self, queue: asyncio.Queue[ExecutionEvent | None]) -> AsyncIterator[ExecutionEvent]:
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def backlog(self) -> list[ExecutionEvent]:
        return list(self._backlog)


class EventBus:
    """Registry of live :class:`ExecutionChannel` objects."""

    def __init__(self) -> None:
        self._channels: dict[str, ExecutionChannel] = {}

    def channel(self, execution_id: str) -> ExecutionChannel:
        channel = self._channels.get(execution_id)
        if channel is None:
            channel = ExecutionChannel(execution_id)
            self._channels[execution_id] = channel
        return channel

    def publish(self, event: ExecutionEvent) -> None:
        if not event.execution_id:
            log.debug("Dropping event without execution_id: %s", event.type)
            return
        self.channel(event.execution_id).publish(event)

    def publish_nowait(self, event: ExecutionEvent) -> None:
        """Alias kept for readability at call sites inside sync code."""
        self.publish(event)

    def is_finished(self, execution_id: str) -> bool:
        channel = self._channels.get(execution_id)
        return bool(channel and channel.closed)

    def backlog(self, execution_id: str) -> list[ExecutionEvent]:
        channel = self._channels.get(execution_id)
        return channel.backlog if channel else []

    def forget(self, execution_id: str) -> None:
        self._channels.pop(execution_id, None)

    def active_executions(self) -> list[str]:
        return [eid for eid, ch in self._channels.items() if not ch.closed]


__all__ = ["EventBus", "ExecutionChannel"]
