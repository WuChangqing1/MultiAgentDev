"""End-to-end verification against the live backends.

Run manually -- it spends real tokens:

    python scripts/verify_live.py

Checks, in order:
1. Health of both model backends.
2. Direct local-model call, including ``reasoning_content`` handling.
3. Demo 1 -- extract + summarise (expects Local Worker delegation).
4. Demo 2 -- a hard problem (expects the MainAgent to do it alone).
5. Telemetry separation: no token/latency string may appear in any prompt.
6. Database persistence of the run.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from api.container import build_container, shutdown_container  # noqa: E402

DEMO_1 = (
    "请分析下面这段内容，提取关键信息并总结。\n"
    "张三今年20岁，是软件工程专业学生，目前正在学习大模型和Agent开发。"
)
DEMO_2 = "证明：对任意正整数 n，n^3 - n 能被 6 整除。"

FORBIDDEN_IN_PROMPTS = (
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "total_tokens",
    "tokens_per_second",
    "latency_ms",
    "execution_id",
)


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


async def run_case(container, title: str, text: str) -> None:
    rule(title)
    conversation = await container.database.create_conversation()
    user_message_id = await container.database.add_message(conversation.id, "user", text)
    execution = await container.orchestrator.start(
        conversation_id=conversation.id, user_input=text, user_message_id=user_message_id
    )

    seen: list[str] = []
    channel = container.event_bus.channel(execution.id)

    async def watch() -> None:
        async with channel.subscribe() as stream:
            async for event in stream:
                if event.type in ("agent_started", "decision") and not any(
                    s.startswith(f"{event.type}:{event.agent}") for s in seen
                ):
                    label = event.agent_label or event.agent or "?"
                    seen.append(f"{event.type}:{event.agent}")
                    print(f"  [{event.type:14s}] {label}  stage={event.stage}")

    watcher = asyncio.create_task(watch())
    outcome = await container.orchestrator.wait(execution.id)
    watcher.cancel()

    print(f"\n  status : {outcome.status}")
    print(f"  answer : {(outcome.final_answer or outcome.error or '')[:600]}")
    print(f"  steps  : {len(outcome.steps)}")
    for step in outcome.steps:
        usage = step.usage
        print(
            f"    {step.step_index:>2}. {step.agent_name:<18} {step.status:<10} "
            f"stage={step.stage:<11} "
            f"tok={getattr(usage, 'total_tokens', None)} "
            f"lat={getattr(usage, 'latency_ms', None)}ms "
            f"tps={getattr(usage, 'tokens_per_second', None)}"
        )

    totals = container.token_tracker.aggregate(execution.id)
    print(
        f"\n  tokens : prompt={totals.prompt_tokens} completion={totals.completion_tokens} "
        f"reasoning={totals.reasoning_tokens} total={totals.total_tokens}"
    )
    print(f"  calls  : {totals.calls}  cost={totals.cost_usd}")

    # Telemetry isolation check across every prompt actually sent.
    leaks: list[str] = []
    for agent in container.registry.all_agents():
        provider = agent.provider
        for messages in getattr(provider, "_debug_prompts", []):
            joined = "\n".join(m.content for m in messages)
            leaks.extend(token for token in FORBIDDEN_IN_PROMPTS if token in joined)
    print(f"  telemetry leaks in prompts: {sorted(set(leaks)) or 'none'}")

    persisted = await container.database.get_execution(execution.id)
    print(f"  persisted: execution={persisted is not None} steps={len(persisted.steps) if persisted else 0}")


async def main() -> None:
    container = await build_container()
    try:
        rule("1. Model health")
        statuses = await container.model_health.statuses(
            container.deepseek, container.local, force=True, include_deepseek=True
        )
        for status in statuses:
            print(f"  {status.provider:<10} {status.name:<16} {status.status:<12} {status.detail}")

        rule("2. Direct local-model call (reasoning_content handling)")
        from providers.base import GenerateOptions, LLMMessage

        response = await container.local.generate(
            [LLMMessage(role="user", content="用一句话说明什么是信息抽取。")],
            GenerateOptions(max_tokens=256, reasoning_effort="low"),
        )
        print(f"  content        : {response.content[:200]!r}")
        print(f"  reasoning      : {(response.reasoning or '')[:200]!r}")
        print(f"  reasoning_tok  : {response.usage.reasoning_tokens} "
              f"({response.usage.reasoning_tokens_source})")
        print(f"  usage          : prompt={response.usage.prompt_tokens} "
              f"completion={response.usage.completion_tokens}")

        await run_case(container, "3. Demo 1 — extract + summarise (expect delegation)", DEMO_1)
        await run_case(container, "4. Demo 2 — hard proof (expect MainAgent does it alone)", DEMO_2)

        rule("5. Session statistics")
        stats = await container.database.aggregate_stats()
        for key in (
            "requests",
            "agent_calls",
            "deepseek_calls",
            "local_calls",
            "total_tokens",
            "local_tokens",
            "cloud_tokens",
            "average_latency_ms",
            "cost_usd",
        ):
            print(f"  {key:<20} {stats.get(key)}")
        print("  by_agent:")
        for name, agg in stats["by_agent"].items():
            print(f"    {name:<20} calls={agg.calls} total={agg.total_tokens}")
    finally:
        await shutdown_container(container)


if __name__ == "__main__":
    asyncio.run(main())
