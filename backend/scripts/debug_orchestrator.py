"""Ad-hoc probe for the orchestrator step-budget path (not part of the suite)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from core.config import Settings  # noqa: E402
from db.database import Database  # noqa: E402
from models.events import ExecutionEvent  # noqa: E402
from orchestration.orchestrator import Orchestrator  # noqa: E402
from orchestration.prompt_loader import PromptRepository  # noqa: E402
from orchestration.registry import build_registry  # noqa: E402
from services.event_bus import EventBus  # noqa: E402
from services.model_health import ModelHealthService  # noqa: E402
from services.runtime_state import RuntimeStateStore  # noqa: E402
from services.token_tracker import TokenTracker  # noqa: E402
from tests.conftest import ScriptedProvider, make_response  # noqa: E402

TMP = BACKEND / ".tmp" / "probe"


async def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(TMP / 'probe.db').as_posix()}",
        deepseek_api_key="test-key",
        max_agent_steps=6,
    )
    db = Database(settings)
    await db.connect()

    local = ScriptedProvider(
        [make_response('{"ok": true}', model="MiniCPM5-2B", provider="llamacpp", is_local=True)],
        name="llamacpp",
        model="MiniCPM5-2B",
        is_local=True,
    )
    delegate = make_response(
        json.dumps({"action": "delegate", "agent": "local_extractor", "task": "again", "context": "x"})
    )
    # Mirrors `test_step_budget_forces_a_final_answer` exactly.
    responses = [delegate] * 6 + [
        make_response(json.dumps({"action": "answer", "answer": "Forced final answer."}))
    ]
    main_provider = ScriptedProvider(responses, name="deepseek", model="deepseek-chat")
    print(f"(settings.max_agent_steps={settings.max_agent_steps}, scripted responses={len(responses)})")

    from agents.main_agent import MainAgent

    registry = build_registry(MainAgent(settings, main_provider, PromptRepository()), local)
    bus = EventBus()
    orch = Orchestrator(
        settings=settings,
        registry=registry,
        database=db,
        event_bus=bus,
        token_tracker=TokenTracker(db, bus),
        runtime_state=RuntimeStateStore(bus),
        model_health=ModelHealthService(),
        local_provider=local,
    )

    conv = await db.create_conversation()
    msg_id = await db.add_message(conv.id, "user", "question")
    execution = await orch.start(conversation_id=conv.id, user_input="question", user_message_id=msg_id)
    outcome = await orch.wait(execution.id)

    print("status       :", outcome.status)
    print("final_answer :", repr(outcome.final_answer))
    print("error        :", outcome.error)
    print("steps        :", [(s.step_index, s.agent_name, s.status) for s in outcome.steps])
    print("main prompts :", len(main_provider.prompts))
    print("responses consumed:", main_provider._index)  # noqa: SLF001 - probe only
    for i, prompt in enumerate(main_provider.prompts):
        user = prompt[1].content
        extra = "FINALIZE" if "ADDITIONAL INSTRUCTION" in user else "plan"
        tail = user.split("===== INTERNAL AGENT STATE =====")[-1].strip().replace("\n", " | ")[:120]
        print(f"  call {i}: {extra:9s} state={tail}")
    for i, step in enumerate(outcome.steps):
        print(
            f"  step {i}: idx={step.step_index} agent={step.agent_name:16s} stage={step.stage:12s} "
            f"status={step.status:9s} task={(step.task or '')[:38]!r} out={(step.output or '')[:52]!r}"
        )
    print("events       :", [e.type for e in bus.backlog(execution.id)][:6], "...")
    await db.disconnect()


asyncio.run(main())
