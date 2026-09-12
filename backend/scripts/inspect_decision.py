"""Dump exactly what the MainAgent is told, and why it decided as it did.

Answers two questions that guessing cannot:
  1. Does the prompt really contain the dynamic worker catalogue?
  2. What reason does the MainAgent give for not delegating?

    python scripts/inspect_decision.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from api.container import build_container, shutdown_container  # noqa: E402
from models.schemas import AgentVisibleState  # noqa: E402

PROMPT = "用 Python 写一个函数判断字符串是否为回文，只给代码。"

#: A long, mechanical extraction job: enough bulk that delegating is genuinely
#: cheaper than doing it inline. Compare its decision with PROMPT's.
BULK_PROMPT = (
    "从下面 6 条设备巡检记录里抽取 设备编号、温度、状态 三个字段，输出 JSON 数组。\n"
    "\n".join(
        f"{i}. 设备 A-10{i}：本次巡检温度 {30 + i} 摄氏度，运行状态正常，"
        f"记录时间 2026-03-0{i} 09:{i}0，巡检员 张伟。"
        for i in range(1, 7)
    )
)


async def decide_for(container, prompt: str, label: str) -> None:
    """Show the catalogue, then one real decision, for a single prompt."""
    main_agent = container.registry.main
    local_ok = await container.model_health.local_available(container.local)

    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print(f"输入: {prompt.splitlines()[0][:70]}...")
    print(f"(共 {len(prompt)} 字符)")

    state = AgentVisibleState(
        user_goal=prompt,
        current_stage="planning",
        available_agents=["main", *container.registry.worker_keys()],
        available_workers=container.registry.worker_catalog(local_available=local_ok),
        local_workers_available=local_ok,
        steps_remaining=12,
        step_index=1,
    )

    turn = await main_agent.decide(user_goal=prompt, agent_state=state)
    decision = turn.decision
    print(f"\n  action  : {decision.action}")
    print(f"  agent   : {decision.agent}")
    print(f"  reason  : {decision.reason}")
    if decision.task:
        print(f"  task    : {decision.task[:140]}")
    if decision.answer:
        print(f"  answer  : {decision.answer[:140].replace(chr(10), ' ')}...")


async def main() -> None:
    container = await build_container()
    try:
        local_ok = await container.model_health.local_available(container.local)
        print(f"local endpoint usable: {local_ok}")

        catalog = container.registry.worker_catalog(local_available=local_ok)
        print("\n=== dynamic worker catalogue shown to the MainAgent ===")
        for key, desc in catalog.items():
            print(f"  {key}: {desc}")

        # Prove the catalogue really reaches the prompt (no hardcoded list).
        messages, user_content = container.registry.main.build_messages(
            user_goal=PROMPT,
            agent_state=AgentVisibleState(
                user_goal=PROMPT,
                available_workers=catalog,
                local_workers_available=local_ok,
            ),
        )
        print(f"\nsystem message: {len(messages[0].content)} chars")
        print("  hardcoded worker catalogue present? ",
              "| `local_coder` |" in messages[0].content)
        print("  catalogue in user-message tail?     ",
              "local_coder" in user_content)

        await decide_for(container, PROMPT, "短任务：3 行代码")
        await decide_for(container, BULK_PROMPT, "长任务：6 条记录批量抽取")
    finally:
        await shutdown_container(container)


if __name__ == "__main__":
    asyncio.run(main())
