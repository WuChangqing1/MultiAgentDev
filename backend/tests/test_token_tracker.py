"""Token tracker / TokenUsage tests, including the honesty rules."""

from __future__ import annotations

from datetime import timedelta

import pytest

from models.token_usage import TokenUsage, TokenUsageAggregate, utcnow
from providers.llama_cpp import estimate_reasoning_tokens
from services.event_bus import EventBus
from services.token_tracker import TokenTracker


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------


def test_total_is_derived_only_when_both_halves_are_known():
    both = TokenUsage(prompt_tokens=100, completion_tokens=50).finalize()
    assert both.total_tokens == 150

    partial = TokenUsage(prompt_tokens=100).finalize()
    assert partial.total_tokens is None, "a total must never be invented from a partial observation"


def test_latency_and_throughput_are_derived_from_timestamps():
    start = utcnow()
    usage = TokenUsage(
        prompt_tokens=10,
        completion_tokens=40,
        request_start_time=start,
        request_end_time=start + timedelta(seconds=2),
    ).finalize()
    assert usage.latency_ms == pytest.approx(2000.0, rel=1e-3)
    assert usage.tokens_per_second == pytest.approx(20.0, rel=1e-3)


def test_missing_fields_stay_null():
    usage = TokenUsage().finalize()
    assert usage.prompt_tokens is None
    assert usage.completion_tokens is None
    assert usage.total_tokens is None
    assert usage.reasoning_tokens is None
    assert usage.cached_tokens is None
    assert usage.cost_usd is None


def test_reasoning_estimation_is_labelled_estimated():
    usage = TokenUsage(
        prompt_tokens=10,
        completion_tokens=10,
        reasoning_tokens=estimate_reasoning_tokens("思考" * 10),
        reasoning_tokens_source="estimated",
    )
    assert usage.reasoning_tokens is not None
    assert usage.reasoning_is_estimated is True


def test_estimate_reasoning_tokens_handles_empty_and_cjk():
    assert estimate_reasoning_tokens(None) is None
    assert estimate_reasoning_tokens("   ") is None
    assert estimate_reasoning_tokens("你好世界") == 4
    assert estimate_reasoning_tokens("a" * 40) == 10


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def test_aggregate_sums_and_averages():
    first = TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120, latency_ms=1000)
    second = TokenUsage(prompt_tokens=200, completion_tokens=40, total_tokens=240, latency_ms=3000)
    agg = TokenUsageAggregate.from_usages([first, second])

    assert agg.calls == 2
    assert agg.prompt_tokens == 300
    assert agg.completion_tokens == 60
    assert agg.total_tokens == 360
    assert agg.latency_ms == 4000
    assert agg.tokens_per_second == pytest.approx(15.0, rel=1e-3)


def test_aggregate_of_nothing_is_empty_not_zero():
    agg = TokenUsageAggregate.from_usages([])
    assert agg.calls == 0
    assert agg.total_tokens is None


def test_aggregate_keeps_none_when_nobody_reported():
    agg = TokenUsageAggregate.from_usages([TokenUsage(), TokenUsage()])
    assert agg.calls == 2
    assert agg.total_tokens is None
    assert agg.reasoning_tokens is None


def test_aggregate_flags_estimated_values():
    estimated = TokenUsage(reasoning_tokens=10, reasoning_tokens_source="estimated")
    agg = TokenUsageAggregate.from_usages([estimated])
    assert agg.any_estimated is True


# --------------------------------------------------------------------------
# Tracker
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracker_records_persists_and_broadcasts(temp_db):
    bus = EventBus()
    tracker = TokenTracker(temp_db, bus)
    execution_id = "exec-1"

    received = []
    channel = bus.channel(execution_id)
    async with channel.subscribe() as stream:
        usage = TokenUsage(
            prompt_tokens=120,
            completion_tokens=83,
            total_tokens=203,
            model="MiniCPM5-2B",
            provider="llamacpp",
            is_local=True,
        )
        await tracker.record(usage, execution_id=execution_id, agent_name="local_extractor")

        async for event in stream:
            received.append(event)
            break

    assert received[0].type == "token_usage"
    assert received[0].usage is not None
    assert received[0].usage.prompt_tokens == 120

    aggregate = tracker.aggregate(execution_id)
    assert aggregate.total_tokens == 203
    assert aggregate.calls == 1

    persisted = await temp_db.list_model_calls(execution_id)
    assert len(persisted) == 1
    assert persisted[0]["prompt_tokens"] == 120
    assert persisted[0]["is_local"] is True
    assert persisted[0]["agent_name"] == "local_extractor"


@pytest.mark.asyncio
async def test_tracker_groups_by_agent(temp_db):
    tracker = TokenTracker(temp_db, EventBus())
    for agent, tokens in (("main", 1000), ("local_extractor", 200), ("local_reviewer", 50)):
        await tracker.record(
            TokenUsage(prompt_tokens=tokens, completion_tokens=0, total_tokens=tokens),
            execution_id="exec-2",
            agent_name=agent,
        )

    by_agent = tracker.by_agent("exec-2")
    assert set(by_agent) == {"main", "local_extractor", "local_reviewer"}
    assert by_agent["main"].total_tokens == 1000
    assert tracker.aggregate("exec-2").total_tokens == 1250


@pytest.mark.asyncio
async def test_tracker_survives_a_database_failure():
    """Telemetry must never take down a request."""

    class BrokenDatabase:
        async def add_model_call(self, **_kwargs):
            raise RuntimeError("disk on fire")

    bus = EventBus()
    tracker = TokenTracker(BrokenDatabase(), bus)

    usage = await tracker.record(
        TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
        execution_id="exec-3",
        agent_name="main",
    )
    assert usage.prompt_tokens == 5
    assert tracker.aggregate("exec-3").total_tokens == 10


@pytest.mark.asyncio
async def test_tracker_without_execution_id_still_persists(temp_db):
    tracker = TokenTracker(temp_db, EventBus())
    await tracker.record(TokenUsage(prompt_tokens=1, completion_tokens=1), execution_id=None, agent_name="main")
    assert tracker.aggregate("anything").calls == 0
