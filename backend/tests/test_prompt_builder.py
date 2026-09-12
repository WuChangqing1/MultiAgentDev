"""Prompt assembly tests.

These tests exist to make the project's central rule executable:

    Context -> prompt | Agent State -> prompt tail | Telemetry -> never a prompt
"""

from __future__ import annotations

import pytest

from core.prompt_builder import (
    STATE_BANNER,
    TelemetryLeakError,
    build_agent_prompt,
    build_worker_prompt,
)
from models.events import ExecutionEvent
from models.schemas import AgentVisibleState, RuntimeTelemetry
from models.token_usage import TokenUsage


def test_prompt_order_is_system_task_context_state():
    prompt = build_agent_prompt(
        "SYSTEM RULES",
        task="do the thing",
        context="some material",
        agent_state=AgentVisibleState(user_goal="goal", current_stage="planning"),
    )
    assert prompt.index("SYSTEM RULES") < prompt.index("TASK")
    assert prompt.index("TASK") < prompt.index("CONTEXT")
    assert prompt.index("CONTEXT") < prompt.index(STATE_BANNER)


def test_agent_state_is_always_last():
    prompt = build_agent_prompt(
        "sys",
        task="task",
        context="ctx",
        agent_state=AgentVisibleState(user_goal="g"),
    )
    # Nothing may follow the state block.
    assert prompt.rindex(STATE_BANNER) > prompt.rindex("CONTEXT")
    assert STATE_BANNER in prompt[-600:]


def test_worker_prompt_carries_no_chat_history():
    prompt = build_worker_prompt("sys", task="extract", context="张三 20 岁")
    assert "CONVERSATION" not in prompt
    assert "张三 20 岁" in prompt


def test_context_is_clamped():
    prompt = build_worker_prompt("sys", task="t", context="x" * 10_000, context_limit=500)
    assert len(prompt) < 1200
    assert "truncated" in prompt


def test_telemetry_object_is_rejected():
    telemetry = RuntimeTelemetry(execution_id="abc", total_tokens=999)
    with pytest.raises(TelemetryLeakError):
        build_agent_prompt("sys", agent_state=telemetry)  # type: ignore[arg-type]


def test_token_usage_is_rejected():
    with pytest.raises(TelemetryLeakError):
        build_agent_prompt("sys", task="t", context=TokenUsage(total_tokens=1))  # type: ignore[arg-type]


def test_execution_event_is_rejected():
    with pytest.raises(TelemetryLeakError):
        build_agent_prompt(ExecutionEvent(type="token_usage", execution_id="x"))  # type: ignore[arg-type]


def test_state_render_excludes_telemetry_vocabulary():
    state = AgentVisibleState(
        user_goal="goal",
        current_stage="extracting",
        completed_steps=["main: planned"],
        important_results={"local_extractor": '{"name":"张三"}'},
        errors=["worker timed out"],
        steps_remaining=3,
        step_index=2,
    )
    rendered = state.render()

    assert STATE_BANNER in rendered
    for forbidden in (
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "latency",
        "tokens_per_second",
        "cost",
        "execution_id",
    ):
        assert forbidden not in rendered, f"{forbidden} leaked into agent-visible state"


def test_agent_visible_state_has_no_telemetry_fields():
    """A structural guard: adding a telemetry field here should fail loudly."""
    fields = set(AgentVisibleState.model_fields)
    forbidden = {
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "latency_ms",
        "tokens_per_second",
        "cost_usd",
        "execution_id",
        "request_start_time",
        "request_end_time",
        "updated_at",
    }
    assert not (fields & forbidden), f"telemetry fields found on AgentVisibleState: {fields & forbidden}"


def test_empty_sections_are_omitted():
    prompt = build_agent_prompt("sys", task=None, context=None, agent_state=None)
    assert prompt == "sys"
    assert "TASK" not in prompt
    assert STATE_BANNER not in prompt


def test_whitespace_is_normalized():
    prompt = build_agent_prompt("  sys  \n\n", task="  t  ", context="\n\n c \n")
    assert prompt.startswith("sys")
    assert "\n\n\n" not in prompt
