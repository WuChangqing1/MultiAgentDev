"""Orchestrator tests.

These drive the real orchestration loop against scripted providers, so routing,
worker dispatch, token accounting, persistence and the step budget are all
exercised without touching a network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.main_agent import MainAgent  # noqa: E402
from core.config import Settings  # noqa: E402
from db.database import Database  # noqa: E402
from orchestration.orchestrator import Orchestrator  # noqa: E402
from orchestration.prompt_loader import PromptRepository  # noqa: E402
from orchestration.registry import build_registry  # noqa: E402
from services.event_bus import EventBus  # noqa: E402
from services.model_health import ModelHealthService  # noqa: E402
from services.runtime_state import RuntimeStateStore  # noqa: E402
from services.token_tracker import TokenTracker  # noqa: E402
from tests.conftest import OfflineProvider, ScriptedProvider, make_response  # noqa: E402


async def make_orchestrator(
    tmp_path: Path,
    *,
    local: object,
    main_responses: list | None = None,
    main_provider: object | None = None,
) -> tuple:
    """Wire a full orchestrator with faked providers.

    Nothing global is touched: the orchestrator receives its settings, so tests
    stay independent of the process-wide settings store.
    """
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'orch.db').as_posix()}",
        deepseek_api_key="test-key",
        max_agent_steps=6,
    )
    database = Database(settings)
    await database.connect()

    prompts = PromptRepository()
    provider = main_provider or ScriptedProvider(
        main_responses or [], name="deepseek", model="deepseek-chat"
    )
    main_agent = MainAgent(settings, provider, prompts)
    registry = build_registry(main_agent, local)

    bus = EventBus()
    tracker = TokenTracker(database, bus)
    runtime = RuntimeStateStore(bus)
    health = ModelHealthService()

    orchestrator = Orchestrator(
        settings=settings,
        registry=registry,
        database=database,
        event_bus=bus,
        token_tracker=tracker,
        runtime_state=runtime,
        model_health=health,
        local_provider=local,
    )
    return orchestrator, database, provider, bus


async def run_chat(orchestrator, database, text: str = "请分析并总结下面这段内容。张三今年20岁。"):
    conversation = await database.create_conversation()
    user_message_id = await database.add_message(conversation.id, "user", text)
    execution = await orchestrator.start(
        conversation_id=conversation.id, user_input=text, user_message_id=user_message_id
    )
    outcome = await orchestrator.wait(execution.id)
    return execution, outcome, conversation


# --------------------------------------------------------------------------
# Happy path: delegate -> answer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegates_to_worker_then_answers(tmp_path):
    local = ScriptedProvider(
        [make_response('{"name": "张三", "age": 20}', model="MiniCPM5-2B", provider="llamacpp", is_local=True)],
        name="llamacpp",
        model="MiniCPM5-2B",
        is_local=True,
    )
    main_responses = [
        make_response(
            json.dumps(
                {
                    "action": "delegate",
                    "agent": "local_extractor",
                    "task": "Extract name and age as JSON.",
                    "context": "张三今年20岁。",
                },
                ensure_ascii=False,
            )
        ),
        make_response(json.dumps({"action": "answer", "answer": "张三，20 岁。"}, ensure_ascii=False)),
    ]

    orchestrator, database, main_provider, bus = await make_orchestrator(
        tmp_path, local=local, main_responses=main_responses
    )
    execution, outcome, conversation = await run_chat(orchestrator, database)

    assert outcome is not None
    assert outcome.status == "completed"
    assert outcome.final_answer == "张三，20 岁。"

    # two main steps + one worker step
    assert [step.agent_name for step in outcome.steps] == [
        "main",
        "local_extractor",
        "main",
    ]
    worker_step = outcome.steps[1]
    assert worker_step.status == "completed"
    assert worker_step.is_local is True
    assert worker_step.parsed_output == {"name": "张三", "age": 20}

    # the worker saw only task + context, never the chat history
    worker_prompt = local.prompts[0][0].content
    assert "CONVERSATION SO FAR" not in worker_prompt
    assert "INTERNAL AGENT STATE" in worker_prompt
    assert "张三今年20岁。" in worker_prompt

    # telemetry was recorded for both models
    calls = await database.list_model_calls(execution.id)
    assert {c["agent_name"] for c in calls} == {"main", "local_extractor"}

    # the assistant message was persisted
    messages = await database.list_messages(conversation.id)
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "张三，20 岁。"

    # a terminal SSE event was published
    types = [e.type for e in bus.backlog(execution.id)]
    assert types[0] == "execution_started"
    assert "agent_started" in types
    assert "token_usage" in types
    assert types[-1] == "execution_completed"

    assert all(p[0].role == "system" for p in main_provider.prompts if p)
    await database.disconnect()


# --------------------------------------------------------------------------
# Fallback: local model offline
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offline_local_model_is_absorbed_by_main(tmp_path):
    main_responses = [
        make_response(
            json.dumps(
                {"action": "delegate", "agent": "local_extractor", "task": "extract", "context": "some text"}
            )
        ),
        make_response(json.dumps({"action": "answer", "answer": "Handled by DeepSeek."})),
    ]
    orchestrator, database, _, bus = await make_orchestrator(
        tmp_path, local=OfflineProvider(), main_responses=main_responses
    )
    execution, outcome, _ = await run_chat(orchestrator, database)

    assert outcome is not None
    # The task still succeeded -- only the main agent ran.
    assert outcome.status == "completed"
    assert outcome.final_answer == "Handled by DeepSeek."
    assert all(step.agent_name == "main" for step in outcome.steps)

    types = [e.type for e in bus.backlog(execution.id)]
    assert "notice" in types
    assert "agent_failed" not in types
    await database.disconnect()


# --------------------------------------------------------------------------
# Malformed decision
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unparseable_decision_degrades_to_an_answer(tmp_path):
    local = ScriptedProvider([make_response("{}")], name="llamacpp", model="MiniCPM5-2B", is_local=True)
    main_responses = [make_response("I am not going to emit JSON today.")]

    orchestrator, database, _, _ = await make_orchestrator(tmp_path, local=local, main_responses=main_responses)
    execution, outcome, _ = await run_chat(orchestrator, database)

    assert outcome is not None
    assert outcome.status == "completed"
    assert "JSON" in (outcome.final_answer or "")
    await database.disconnect()


# --------------------------------------------------------------------------
# Step budget
# --------------------------------------------------------------------------


PLAIN_ANSWER = "## 结论\n\n这是最后一次委派后由 MainAgent 直接给出的正式回答。"


class AlwaysDelegatingProvider(ScriptedProvider):
    """A MainAgent that never stops delegating until it is forced to answer.

    Behaviour is keyed off the prompt itself rather than a call counter, so the
    test states the contract ("if you ask for plain text, you get plain text")
    instead of hard-coding an index into the orchestrator's call sequence.
    """

    def __init__(self) -> None:
        super().__init__([], name="deepseek", model="deepseek-chat")
        self.decisions = 0
        self.streams_seen = 0

    @staticmethod
    def _reply(messages) -> str:
        prompt = "\n".join(m.content for m in messages)
        if "plain Markdown text" in prompt:
            return PLAIN_ANSWER
        return json.dumps(
            {"action": "delegate", "agent": "local_extractor", "task": "again", "context": "x"}
        )

    async def generate(self, messages, options=None):
        self.prompts.append(list(messages))
        self.options.append(options or GenerateOptions())
        self.decisions += 1
        content = self._reply(messages)
        self.calls.append((self.decisions - 1, content[:60]))
        return make_response(content, model="deepseek-chat", provider="deepseek")

    async def stream(self, messages, options=None):
        self.prompts.append(list(messages))
        self.options.append(options or GenerateOptions())
        self.streams_seen += 1
        self.streams.append(self._reply(messages)[:40])
        yield self._reply(messages), None


@pytest.mark.asyncio
async def test_step_budget_forces_a_final_answer(tmp_path):
    """A model that delegates forever must be stopped and made to answer."""
    local = ScriptedProvider(
        [make_response('{"ok": true}', model="MiniCPM5-2B", provider="llamacpp", is_local=True)],
        name="llamacpp",
        model="MiniCPM5-2B",
        is_local=True,
    )
    main_provider = AlwaysDelegatingProvider()

    orchestrator, database, provider, bus = await make_orchestrator(
        tmp_path, local=local, main_provider=main_provider
    )
    execution, outcome, _ = await run_chat(orchestrator, database)

    assert outcome is not None
    assert outcome.status == "completed", f"error={outcome.error} calls={provider.decisions}"
    assert PLAIN_ANSWER.strip() in (outcome.final_answer or "")

    # The last rung abandons the JSON protocol and streams plain text.
    assert provider.streams_seen == 1

    # Planning respects the budget; only finalisation may extend the timeline.
    planning_steps = [s for s in outcome.steps if s.stage != "finalizing"]
    assert len(planning_steps) <= 6

    # Step indices are unique and contiguous across the whole timeline.
    indices = [s.step_index for s in outcome.steps]
    assert indices == list(range(1, len(indices) + 1))

    notices = [e.message for e in bus.backlog(execution.id) if e.type == "notice"]
    assert any("budget" in (n or "").lower() for n in notices)
    await database.disconnect()


# --------------------------------------------------------------------------
# Worker failure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_failure_does_not_fail_the_task(tmp_path):
    class ExplodingProvider(ScriptedProvider):
        """Reports healthy but blows up when actually asked to generate."""

        async def generate(self, messages, options=None):
            raise RuntimeError("kaboom")

    main_responses = [
        make_response(
            json.dumps({"action": "delegate", "agent": "local_summarizer", "task": "sum", "context": "text"})
        ),
        make_response(json.dumps({"action": "answer", "answer": "Done without the worker."})),
    ]
    orchestrator, database, _, _ = await make_orchestrator(
        tmp_path,
        local=ExplodingProvider([], name="llamacpp", model="MiniCPM5-2B", is_local=True),
        main_responses=main_responses,
    )
    execution, outcome, _ = await run_chat(orchestrator, database)

    assert outcome is not None
    assert outcome.status == "completed"
    assert outcome.final_answer == "Done without the worker."
    worker_steps = [s for s in outcome.steps if s.agent_name == "local_summarizer"]
    assert len(worker_steps) == 1
    assert worker_steps[0].status == "failed"
    assert "kaboom" in (worker_steps[0].error or "")
    await database.disconnect()


# --------------------------------------------------------------------------
# Telemetry isolation, end to end
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_telemetry_reaches_any_prompt(tmp_path):
    local = ScriptedProvider(
        [make_response('{"name": "张三"}', model="MiniCPM5-2B", provider="llamacpp", is_local=True)],
        name="llamacpp",
        model="MiniCPM5-2B",
        is_local=True,
    )
    main_responses = [
        make_response(
            json.dumps({"action": "delegate", "agent": "local_extractor", "task": "extract", "context": "张三"})
        ),
        make_response(json.dumps({"action": "answer", "answer": "ok"})),
    ]
    orchestrator, database, main_provider, _ = await make_orchestrator(
        tmp_path, local=local, main_responses=main_responses
    )
    await run_chat(orchestrator, database)

    forbidden = (
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "tokens_per_second",
        "latency_ms",
        "execution_id",
    )
    for provider in (main_provider, local):
        for messages in provider.prompts:
            joined = "\n".join(m.content for m in messages)
            for token in forbidden:
                assert token not in joined, f"telemetry key {token!r} leaked into a prompt"

    await database.disconnect()


@pytest.mark.asyncio
async def test_history_is_windowed_and_digested(tmp_path):
    local = ScriptedProvider([make_response("{}")], name="llamacpp", model="MiniCPM5-2B", is_local=True)
    orchestrator, database, main_provider, _ = await make_orchestrator(
        tmp_path, local=local, main_responses=[make_response(json.dumps({"action": "answer", "answer": "ok"}))]
    )

    conversation = await database.create_conversation()
    from orchestration.context_manager import DIGEST_BUDGET, HISTORY_WINDOW

    # Enough long, older turns to force the digest to compress.
    for i in range(HISTORY_WINDOW + 4):
        role = "user" if i % 2 == 0 else "assistant"
        await database.add_message(conversation.id, role, f"OLD-TURN-{i} " + "filler " * 40)
    for i in range(HISTORY_WINDOW):
        role = "user" if i % 2 == 0 else "assistant"
        await database.add_message(conversation.id, role, f"RECENT-TURN-{i}")

    user_message_id = await database.add_message(conversation.id, "user", "final question")
    execution = await orchestrator.start(
        conversation_id=conversation.id, user_input="final question", user_message_id=user_message_id
    )
    await orchestrator.wait(execution.id)

    user_content = main_provider.prompts[0][1].content
    assert "final question" in user_content

    # The newest turns are verbatim; older ones are compressed into a digest.
    verbatim = user_content.split("Earlier turns")[0]
    digest = user_content.split("Earlier turns", 1)[1]

    assert "RECENT-TURN-0" in verbatim
    assert "OLD-TURN-0" not in verbatim, "turns outside the window must not be sent verbatim"
    assert "[middle omitted]" in digest, "the digest should be bounded"
    assert len(digest) < DIGEST_BUDGET + 200, "the digest must respect its budget"
    await database.disconnect()
