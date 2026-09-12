"""Async SQLite persistence layer.

Engine + session factory only; all query logic lives in :class:`Database`'s
repository methods so callers never hand-write SQL.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import Settings, ensure_data_dir
from db.models import (
    AgentStepRow,
    Base,
    ConversationRow,
    ExecutionRow,
    MessageRow,
    ModelCallRow,
    new_id,
)
from models.schemas import AgentStep, Conversation, Execution, Message
from models.token_usage import TokenUsage

log = logging.getLogger(__name__)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite round-trips naive datetimes; re-attach UTC so the UI is correct."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class Database:
    """Thin async repository over the SQLite file."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    # -- lifecycle ---------------------------------------------------------
    async def connect(self) -> None:
        url = self._settings.database_url
        if url.startswith("sqlite") and ":memory:" not in url:
            ensure_data_dir()
            # Make sure the parent directory of the configured file exists.
            raw_path = url.split("///")[-1]
            try:
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
            except OSError:  # pragma: no cover - unusual paths
                log.warning("Could not pre-create database directory", exc_info=True)

        self._engine = create_async_engine(url, echo=False, future=True)

        if url.startswith("sqlite"):

            @event.listens_for(self._engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - driver hook
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        log.info("database_ready", extra={"url_kind": url.split(":")[0]})

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    @property
    def ready(self) -> bool:
        return self._session_factory is not None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Database.connect() must be awaited before use")
        async with self._session_factory() as session:
            yield session

    async def ping(self) -> bool:
        try:
            async with self.session() as session:
                await session.execute(select(func.count()).select_from(ConversationRow))
            return True
        except Exception:  # noqa: BLE001 - health check
            log.warning("database_ping_failed", exc_info=True)
            return False

    # -- conversations -----------------------------------------------------
    async def create_conversation(self, title: str = "New conversation") -> Conversation:
        row = ConversationRow(id=new_id(), title=title[:200] or "New conversation")
        async with self.session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _to_conversation(row, message_count=0)

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        async with self.session() as session:
            row = await session.get(ConversationRow, conversation_id)
            if row is None:
                return None
            count = await session.scalar(
                select(func.count()).select_from(MessageRow).where(MessageRow.conversation_id == conversation_id)
            )
        return _to_conversation(row, message_count=int(count or 0))

    async def list_conversations(self, limit: int = 100) -> list[Conversation]:
        async with self.session() as session:
            rows = (
                await session.execute(
                    select(ConversationRow).order_by(ConversationRow.updated_at.desc()).limit(limit)
                )
            ).scalars().all()
            counts = dict(
                (
                    await session.execute(
                        select(MessageRow.conversation_id, func.count()).group_by(MessageRow.conversation_id)
                    )
                ).all()
            )
        return [_to_conversation(r, message_count=int(counts.get(r.id, 0))) for r in rows]

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self.session() as session:
            row = await session.get(ConversationRow, conversation_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
        return True

    async def ensure_conversation(self, conversation_id: str | None) -> Conversation:
        """Return the conversation, creating one when the id is unknown."""
        if conversation_id:
            existing = await self.get_conversation(conversation_id)
            if existing is not None:
                return existing
        return await self.create_conversation()

    async def rename_conversation(self, conversation_id: str, title: str) -> None:
        async with self.session() as session:
            await session.execute(
                update(ConversationRow)
                .where(ConversationRow.id == conversation_id)
                .values(title=title[:200], updated_at=datetime.now(timezone.utc))
            )
            await session.commit()

    # -- messages ----------------------------------------------------------
    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        reasoning: str | None = None,
        execution_id: str | None = None,
        usage: TokenUsage | None = None,
    ) -> int:
        row = MessageRow(
            conversation_id=conversation_id,
            role=role,
            content=content,
            reasoning=reasoning,
            execution_id=execution_id,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            latency_ms=usage.latency_ms if usage else None,
        )
        async with self.session() as session:
            session.add(row)
            await session.flush()
            await session.execute(
                update(ConversationRow)
                .where(ConversationRow.id == conversation_id)
                .values(updated_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return int(row.id)

    async def list_messages(self, conversation_id: str, limit: int = 500) -> list[Message]:
        async with self.session() as session:
            rows = (
                await session.execute(
                    select(MessageRow)
                    .where(MessageRow.conversation_id == conversation_id)
                    .order_by(MessageRow.id.asc())
                    .limit(limit)
                )
            ).scalars().all()
        return [_to_message(r) for r in rows]

    async def recent_messages(self, conversation_id: str, limit: int = 6) -> list[Message]:
        """Newest ``limit`` messages, returned oldest-first.

        Used to give the MainAgent a bounded view of the conversation; the full
        history is never forwarded to workers.
        """
        async with self.session() as session:
            rows = (
                await session.execute(
                    select(MessageRow)
                    .where(MessageRow.conversation_id == conversation_id)
                    .order_by(MessageRow.id.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [_to_message(r) for r in reversed(rows)]

    # -- executions --------------------------------------------------------
    async def create_execution(
        self,
        conversation_id: str,
        user_input: str,
        *,
        user_message_id: int | None = None,
    ) -> Execution:
        row = ExecutionRow(
            id=new_id(),
            conversation_id=conversation_id,
            user_input=user_input,
            user_message_id=user_message_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        async with self.session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _to_execution(row, steps=[])

    async def finish_execution(
        self,
        execution_id: str,
        *,
        status: str,
        final_answer: str | None = None,
        error: str | None = None,
        reasoning: str | None = None,
        assistant_message_id: int | None = None,
        step_count: int | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        async with self.session() as session:
            values: dict[str, Any] = {
                "status": status,
                "finished_at": datetime.now(timezone.utc),
                "final_answer": final_answer,
                "error": error,
                "reasoning": reasoning,
            }
            if assistant_message_id is not None:
                values["assistant_message_id"] = assistant_message_id
            if step_count is not None:
                values["step_count"] = step_count
            if usage is not None:
                values.update(
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    reasoning_tokens=usage.reasoning_tokens,
                    total_tokens=usage.total_tokens,
                    cost_usd=usage.cost_usd,
                )
            await session.execute(update(ExecutionRow).where(ExecutionRow.id == execution_id).values(**values))
            await session.commit()

    async def get_execution(self, execution_id: str) -> Execution | None:
        async with self.session() as session:
            row = await session.get(ExecutionRow, execution_id)
            if row is None:
                return None
            steps = (
                await session.execute(
                    select(AgentStepRow)
                    .where(AgentStepRow.execution_id == execution_id)
                    .order_by(AgentStepRow.step_index.asc())
                )
            ).scalars().all()
            calls = (
                await session.execute(
                    select(ModelCallRow)
                    .where(ModelCallRow.execution_id == execution_id)
                    .order_by(ModelCallRow.id.asc())
                )
            ).scalars().all()

        # Usage lives in `model_calls`; fold it back onto the steps so a
        # reopened conversation shows the same numbers the live run showed.
        usage_by_step: dict[int, list[TokenUsage]] = {}
        for call in calls:
            if call.agent_step_id is None:
                continue
            usage_by_step.setdefault(call.agent_step_id, []).append(_call_to_usage(call))

        step_models: list[AgentStep] = []
        for step_row in steps:
            step = _to_step(step_row)
            attached = usage_by_step.get(int(step_row.id or 0))
            if attached:
                step.usage = _combine_usages(attached)
            step_models.append(step)

        execution = _to_execution(row, steps=step_models)
        execution.usage = TokenUsage(
            agent_name="execution",
            model=None,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            reasoning_tokens=row.reasoning_tokens,
            total_tokens=row.total_tokens,
            cost_usd=row.cost_usd,
        )
        return execution

    async def list_executions(self, conversation_id: str, limit: int = 50) -> list[Execution]:
        async with self.session() as session:
            rows = (
                await session.execute(
                    select(ExecutionRow)
                    .where(ExecutionRow.conversation_id == conversation_id)
                    .order_by(ExecutionRow.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [_to_execution(r, steps=[]) for r in rows]

    # -- steps -------------------------------------------------------------
    async def add_step(self, step: AgentStep) -> int:
        row = AgentStepRow(
            execution_id=step.execution_id,
            step_index=step.step_index,
            agent_name=step.agent_name,
            agent_label=step.agent_label,
            model=step.model,
            is_local=step.is_local,
            task=step.task,
            stage=step.stage,
            status=step.status,
            input=step.input,
            output=step.output,
            reasoning=step.reasoning,
            parsed_output=step.parsed_output,
            error=step.error,
            debug=step.debug,
            started_at=step.started_at,
            finished_at=step.finished_at,
        )
        async with self.session() as session:
            session.add(row)
            await session.flush()
            step_id = int(row.id)
            await session.execute(
                update(ExecutionRow)
                .where(ExecutionRow.id == step.execution_id)
                .values(step_count=step.step_index + 1)
            )
            await session.commit()
        return step_id

    async def update_step(self, step_id: int, **values: Any) -> None:
        if not values:
            return
        async with self.session() as session:
            await session.execute(update(AgentStepRow).where(AgentStepRow.id == step_id).values(**values))
            await session.commit()

    async def list_steps(self, execution_id: str) -> list[AgentStep]:
        async with self.session() as session:
            rows = (
                await session.execute(
                    select(AgentStepRow)
                    .where(AgentStepRow.execution_id == execution_id)
                    .order_by(AgentStepRow.step_index.asc())
                )
            ).scalars().all()
        return [_to_step(r) for r in rows]

    # -- model calls -------------------------------------------------------
    async def add_model_call(
        self,
        *,
        execution_id: str | None,
        agent_step_id: int | None,
        agent_name: str,
        usage: TokenUsage,
    ) -> int:
        row = ModelCallRow(
            execution_id=execution_id,
            agent_step_id=agent_step_id,
            agent_name=agent_name,
            model=usage.model or "",
            provider=usage.provider or "",
            is_local=usage.is_local,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            total_tokens=usage.total_tokens,
            cached_tokens=usage.cached_tokens,
            reasoning_tokens_source=usage.reasoning_tokens_source,
            cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms,
            tokens_per_second=usage.tokens_per_second,
            request_start_time=usage.request_start_time,
            request_end_time=usage.request_end_time,
            retries=usage.retries,
        )
        async with self.session() as session:
            session.add(row)
            await session.flush()
            call_id = int(row.id)
            await session.commit()
        return call_id

    async def list_model_calls(self, execution_id: str) -> list[dict[str, Any]]:
        async with self.session() as session:
            rows = (
                await session.execute(
                    select(ModelCallRow)
                    .where(ModelCallRow.execution_id == execution_id)
                    .order_by(ModelCallRow.id.asc())
                )
            ).scalars().all()
        return [
            {
                "id": r.id,
                "agent_step_id": r.agent_step_id,
                "agent_name": r.agent_name,
                "model": r.model,
                "provider": r.provider,
                "is_local": r.is_local,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "reasoning_tokens": r.reasoning_tokens,
                "total_tokens": r.total_tokens,
                "cached_tokens": r.cached_tokens,
                "reasoning_tokens_source": r.reasoning_tokens_source,
                "cost_usd": r.cost_usd,
                "latency_ms": r.latency_ms,
                "tokens_per_second": r.tokens_per_second,
                "retries": r.retries,
            }
            for r in rows
        ]

    # -- statistics --------------------------------------------------------
    async def aggregate_stats(self) -> dict[str, Any]:
        """Session-wide telemetry roll-up for ``GET /api/stats``."""
        async with self.session() as session:
            requests = int(await session.scalar(select(func.count()).select_from(ExecutionRow)) or 0)
            call_rows = (await session.execute(select(ModelCallRow))).scalars().all()

        usages: list[TokenUsage] = []
        by_agent: dict[str, list[TokenUsage]] = {}
        for row in call_rows:
            usage = TokenUsage(
                agent_name=row.agent_name,
                model=row.model,
                provider=row.provider,
                is_local=row.is_local,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                reasoning_tokens=row.reasoning_tokens,
                total_tokens=row.total_tokens,
                cached_tokens=row.cached_tokens,
                latency_ms=row.latency_ms,
                tokens_per_second=row.tokens_per_second,
                cost_usd=row.cost_usd,
                reasoning_tokens_source=(row.reasoning_tokens_source or "unavailable"),  # type: ignore[arg-type]
            )
            usages.append(usage)
            by_agent.setdefault(row.agent_name, []).append(usage)

        from models.token_usage import TokenUsageAggregate

        overall = TokenUsageAggregate.from_usages(usages)

        def _sum(predicate) -> int | None:
            vals = [u.total_tokens for u in usages if predicate(u) and u.total_tokens is not None]
            return sum(vals) if vals else None

        return {
            "requests": requests,
            "agent_calls": len(usages),
            "deepseek_calls": sum(1 for u in usages if not u.is_local),
            "local_calls": sum(1 for u in usages if u.is_local),
            "total_tokens": overall.total_tokens,
            "local_tokens": _sum(lambda u: u.is_local),
            "cloud_tokens": _sum(lambda u: not u.is_local),
            "prompt_tokens": overall.prompt_tokens,
            "completion_tokens": overall.completion_tokens,
            "reasoning_tokens": overall.reasoning_tokens,
            "average_latency_ms": (
                round(overall.latency_ms / len(usages), 2) if overall.latency_ms is not None and usages else None
            ),
            "cost_usd": overall.cost_usd,
            "by_agent": {name: TokenUsageAggregate.from_usages(items) for name, items in by_agent.items()},
        }


# --------------------------------------------------------------------------
# Row -> schema mappers
# --------------------------------------------------------------------------


def _to_conversation(row: ConversationRow, *, message_count: int) -> Conversation:
    return Conversation(
        id=row.id,
        title=row.title,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        message_count=message_count,
    )


def _to_message(row: MessageRow) -> Message:
    usage = None
    if row.prompt_tokens is not None or row.completion_tokens is not None:
        usage = TokenUsage(
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            reasoning_tokens=row.reasoning_tokens,
            total_tokens=row.total_tokens,
            latency_ms=row.latency_ms,
        ).finalize()
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        reasoning=row.reasoning,
        execution_id=row.execution_id,
        created_at=_as_utc(row.created_at),
        usage=usage,
    )


def _to_step(row: AgentStepRow) -> AgentStep:
    return AgentStep(
        id=row.id,
        execution_id=row.execution_id,
        step_index=row.step_index,
        agent_name=row.agent_name,
        agent_label=row.agent_label,
        model=row.model,
        is_local=row.is_local,
        task=row.task or "",
        status=row.status,  # type: ignore[arg-type]
        stage=row.stage or "",
        input=row.input,
        output=row.output,
        reasoning=row.reasoning,
        parsed_output=row.parsed_output,
        error=row.error,
        debug=row.debug,
        started_at=_as_utc(row.started_at),
        finished_at=_as_utc(row.finished_at),
    )


def _call_to_usage(row: ModelCallRow) -> TokenUsage:
    """Rebuild a :class:`TokenUsage` from a persisted model call."""
    return TokenUsage(
        agent_name=row.agent_name,
        model=row.model,
        provider=row.provider,
        is_local=row.is_local,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        reasoning_tokens=row.reasoning_tokens,
        total_tokens=row.total_tokens,
        cached_tokens=row.cached_tokens,
        reasoning_tokens_source=(row.reasoning_tokens_source or "unavailable"),  # type: ignore[arg-type]
        cost_usd=row.cost_usd,
        latency_ms=row.latency_ms,
        tokens_per_second=row.tokens_per_second,
        request_start_time=_as_utc(row.request_start_time),
        request_end_time=_as_utc(row.request_end_time),
        retries=row.retries,
    )


def _combine_usages(usages: list[TokenUsage]) -> TokenUsage:
    """Sum the model calls that belong to one timeline step."""
    if len(usages) == 1:
        return usages[0]

    def _sum(field: str) -> int | None:
        values = [getattr(u, field) for u in usages if getattr(u, field) is not None]
        return sum(values) if values else None

    latencies = [u.latency_ms for u in usages if u.latency_ms is not None]
    costs = [u.cost_usd for u in usages if u.cost_usd is not None]
    completion = _sum("completion_tokens")
    total_latency = round(sum(latencies), 2) if latencies else None

    return TokenUsage(
        agent_name=usages[0].agent_name,
        model=usages[0].model,
        provider=usages[0].provider,
        is_local=usages[0].is_local,
        prompt_tokens=_sum("prompt_tokens"),
        completion_tokens=completion,
        reasoning_tokens=_sum("reasoning_tokens"),
        total_tokens=_sum("total_tokens"),
        cached_tokens=_sum("cached_tokens"),
        reasoning_tokens_source=(
            "estimated"
            if any(u.reasoning_tokens_source == "estimated" for u in usages)
            else usages[0].reasoning_tokens_source
        ),
        cost_usd=round(sum(costs), 8) if costs else None,
        latency_ms=total_latency,
        tokens_per_second=(
            round(completion / (total_latency / 1000.0), 2)
            if completion is not None and total_latency
            else None
        ),
        retries=sum(u.retries for u in usages),
    )


def _to_execution(row: ExecutionRow, *, steps: list[AgentStep]) -> Execution:
    return Execution(
        id=row.id,
        conversation_id=row.conversation_id,
        user_message_id=row.user_message_id,
        assistant_message_id=row.assistant_message_id,
        status=row.status,  # type: ignore[arg-type]
        user_input=row.user_input,
        final_answer=row.final_answer,
        error=row.error,
        reasoning=row.reasoning,
        started_at=_as_utc(row.started_at),
        finished_at=_as_utc(row.finished_at),
        step_count=row.step_count or len(steps),
        steps=steps,
    )


__all__ = ["Database"]
