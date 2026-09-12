"""Database layer tests: persistence, history and telemetry roll-up."""

from __future__ import annotations

import pytest

from models.schemas import AgentStep
from models.token_usage import TokenUsage, utcnow


@pytest.mark.asyncio
async def test_conversation_and_message_round_trip(temp_db):
    conversation = await temp_db.create_conversation("Test chat")
    assert conversation.id

    await temp_db.add_message(conversation.id, "user", "hello")
    await temp_db.add_message(conversation.id, "assistant", "hi there")

    messages = await temp_db.list_messages(conversation.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hello"

    reloaded = await temp_db.get_conversation(conversation.id)
    assert reloaded is not None
    assert reloaded.message_count == 2


@pytest.mark.asyncio
async def test_conversations_listed_newest_first(temp_db):
    first = await temp_db.create_conversation("first")
    await temp_db.add_message(first.id, "user", "one")
    second = await temp_db.create_conversation("second")
    await temp_db.add_message(second.id, "user", "two")

    listed = await temp_db.list_conversations()
    assert listed[0].id == second.id


@pytest.mark.asyncio
async def test_ensure_conversation_creates_when_missing(temp_db):
    created = await temp_db.ensure_conversation(None)
    assert created.id
    same = await temp_db.ensure_conversation(created.id)
    assert same.id == created.id
    fresh = await temp_db.ensure_conversation("does-not-exist")
    assert fresh.id != created.id


@pytest.mark.asyncio
async def test_execution_and_steps_persist(temp_db):
    conversation = await temp_db.create_conversation()
    message_id = await temp_db.add_message(conversation.id, "user", "question")
    execution = await temp_db.create_execution(conversation.id, "question", user_message_id=message_id)

    step_id = await temp_db.add_step(
        AgentStep(
            execution_id=execution.id,
            step_index=0,
            agent_name="main",
            agent_label="DeepSeek MainAgent",
            model="deepseek-chat",
            task="plan",
            status="running",
            started_at=utcnow(),
        )
    )
    await temp_db.update_step(step_id, status="completed", output='{"action":"answer"}')

    await temp_db.finish_execution(
        execution.id,
        status="completed",
        final_answer="the answer",
        step_count=1,
        usage=TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120).finalize(),
    )

    loaded = await temp_db.get_execution(execution.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.final_answer == "the answer"
    assert loaded.step_count == 1
    assert len(loaded.steps) == 1
    assert loaded.steps[0].agent_name == "main"
    assert loaded.steps[0].status == "completed"
    assert loaded.usage is not None
    assert loaded.usage.total_tokens == 120


@pytest.mark.asyncio
async def test_model_calls_and_stats_aggregation(temp_db):
    conversation = await temp_db.create_conversation()
    execution = await temp_db.create_execution(conversation.id, "q")

    await temp_db.add_model_call(
        execution_id=execution.id,
        agent_step_id=None,
        agent_name="main",
        usage=TokenUsage(
            model="deepseek-chat",
            provider="deepseek",
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            latency_ms=1500,
        ).finalize(),
    )
    await temp_db.add_model_call(
        execution_id=execution.id,
        agent_step_id=None,
        agent_name="local_extractor",
        usage=TokenUsage(
            model="MiniCPM5-2B",
            provider="llamacpp",
            is_local=True,
            prompt_tokens=200,
            completion_tokens=50,
            total_tokens=250,
            latency_ms=500,
        ).finalize(),
    )

    stats = await temp_db.aggregate_stats()
    assert stats["requests"] == 1
    assert stats["agent_calls"] == 2
    assert stats["deepseek_calls"] == 1
    assert stats["local_calls"] == 1
    assert stats["total_tokens"] == 1450
    assert stats["local_tokens"] == 250
    assert stats["cloud_tokens"] == 1200
    assert stats["average_latency_ms"] == pytest.approx(1000.0, rel=1e-3)
    assert set(stats["by_agent"]) == {"main", "local_extractor"}
    assert stats["by_agent"]["main"].total_tokens == 1200


@pytest.mark.asyncio
async def test_delete_conversation_cascades(temp_db):
    conversation = await temp_db.create_conversation()
    await temp_db.add_message(conversation.id, "user", "bye")
    execution = await temp_db.create_execution(conversation.id, "bye")
    await temp_db.add_step(
        AgentStep(execution_id=execution.id, step_index=0, agent_name="main", task="t")
    )

    assert await temp_db.delete_conversation(conversation.id) is True
    assert await temp_db.get_conversation(conversation.id) is None
    assert await temp_db.get_execution(execution.id) is None


@pytest.mark.asyncio
async def test_recent_messages_returns_oldest_first(temp_db):
    conversation = await temp_db.create_conversation()
    for i in range(10):
        await temp_db.add_message(conversation.id, "user", f"m{i}")

    recent = await temp_db.recent_messages(conversation.id, limit=3)
    assert [m.content for m in recent] == ["m7", "m8", "m9"]


@pytest.mark.asyncio
async def test_step_usage_is_restored_from_model_calls(temp_db):
    """Reopening a conversation must show the same numbers the live run showed.

    Usage is stored in `model_calls`, so `get_execution` has to fold it back
    onto the steps; otherwise a reloaded timeline shows blank token counts.
    """
    conversation = await temp_db.create_conversation()
    execution = await temp_db.create_execution(conversation.id, "q")
    step_id = await temp_db.add_step(
        AgentStep(
            execution_id=execution.id,
            step_index=1,
            agent_name="main",
            model="deepseek-chat",
            task="plan",
            status="completed",
            started_at=utcnow(),
            finished_at=utcnow(),
        )
    )

    for prompt, completion in ((100, 20), (50, 10)):
        await temp_db.add_model_call(
            execution_id=execution.id,
            agent_step_id=step_id,
            agent_name="main",
            usage=TokenUsage(
                model="deepseek-chat",
                provider="deepseek",
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                latency_ms=500,
            ).finalize(),
        )

    loaded = await temp_db.get_execution(execution.id)
    assert loaded is not None
    assert len(loaded.steps) == 1
    usage = loaded.steps[0].usage
    assert usage is not None
    # Both calls for the step are summed, not just the last one.
    assert usage.prompt_tokens == 150
    assert usage.completion_tokens == 30
    assert usage.total_tokens == 180
    assert usage.latency_ms == 1000


@pytest.mark.asyncio
async def test_aggregate_stats_feeds_the_stats_schema(temp_db):
    """`GET /api/stats` must be constructible from `aggregate_stats` output."""
    from models.schemas import StatsResponse

    conversation = await temp_db.create_conversation()
    execution = await temp_db.create_execution(conversation.id, "q")
    await temp_db.add_model_call(
        execution_id=execution.id,
        agent_step_id=None,
        agent_name="main",
        usage=TokenUsage(
            model="deepseek-chat",
            provider="deepseek",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            latency_ms=400,
        ).finalize(),
    )

    raw = await temp_db.aggregate_stats()
    response = StatsResponse(**raw)
    assert response.requests == 1
    assert response.agent_calls == 1
    assert response.by_agent["main"].total_tokens == 120
    assert response.by_agent["main"].calls == 1


@pytest.mark.asyncio
async def test_ping_reports_health(temp_db):
    assert await temp_db.ping() is True
