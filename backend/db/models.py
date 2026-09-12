"""SQLAlchemy 2.0 ORM models.

Schema mirrors requirement #36: conversations / messages / executions /
agent_steps / model_calls.

Telemetry columns live here (tokens, latency, timestamps). They are read back
into Pydantic *telemetry* objects and are never re-injected into a prompt --
:mod:`core.prompt_builder` is the only prompt path and does not accept them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationRow(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[MessageRow]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", lazy="selectin"
    )
    executions: Mapped[list[ExecutionRow]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class MessageRow(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # Denormalized roll-up of the execution's token usage (telemetry only).
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    conversation: Mapped[ConversationRow] = relationship(back_populates="messages")


class ExecutionRow(Base, TimestampMixin):
    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assistant_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    user_input: Mapped[str] = mapped_column(Text, default="")
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)

    # Telemetry roll-up for fast stats queries.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    conversation: Mapped[ConversationRow] = relationship(back_populates="executions")
    steps: Mapped[list[AgentStepRow]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentStepRow.step_index",
    )


class AgentStepRow(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, default=0)

    agent_name: Mapped[str] = mapped_column(String(48), index=True)
    agent_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(96), nullable=True)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)

    task: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)

    input: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Debug extras (raw model text, parse errors, retries) -- Debug Mode only.
    debug: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution: Mapped[ExecutionRow] = relationship(back_populates="steps")
    model_calls: Mapped[list[ModelCallRow]] = relationship(
        back_populates="agent_step", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_agent_steps_exec_idx", "execution_id", "step_index"),)


class ModelCallRow(Base):
    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="CASCADE"), nullable=True, index=True
    )
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    agent_name: Mapped[str] = mapped_column(String(48), index=True)
    model: Mapped[str] = mapped_column(String(96), default="")
    provider: Mapped[str] = mapped_column(String(32), default="")
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reasoning_tokens_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    agent_step: Mapped[AgentStepRow | None] = relationship(back_populates="model_calls")


__all__ = [
    "AgentStepRow",
    "Base",
    "ConversationRow",
    "ExecutionRow",
    "MessageRow",
    "ModelCallRow",
    "new_id",
]
