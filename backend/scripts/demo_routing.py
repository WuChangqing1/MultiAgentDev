"""Why the workbench delegates to some tasks and not others.

Runs a spread of prompts through the real orchestrator and prints the execution
chain plus the MainAgent's own stated reason. The point is to show that routing
is a judgement, not a fixed pipeline -- and to make that judgement inspectable.

Run with the backend stopped (it uses its own database):
    python scripts/demo_routing.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from api.container import build_container, shutdown_container  # noqa: E402

BULK = "从下面 6 条设备巡检记录里抽取 设备编号、温度、状态 三个字段，输出 JSON 数组。\n" + "\n".join(
    f"{i}. 设备 A-10{i}：本次巡检温度 {30 + i} 摄氏度，运行状态正常，"
    f"记录时间 2026-03-0{i} 09:{i}0，巡检员 张伟。"
    for i in range(1, 7)
)

CASES = [
    ("A. 短任务：3 行代码", "用 Python 写一个函数判断字符串是否为回文，只给代码。", "自己做更划算"),
    ("B. 长任务：6 条记录批量抽取", BULK, "委派更划算"),
    ("C. 复杂推理：数学证明", "证明：对任意正整数 n，n^3 - n 能被 6 整除。", "自己做（小模型做不到）"),
    (
        "D. 需要对话历史与判断",
        "把我们刚才聊的内容用一句话概括一下，然后说说你认为我下一步最该做什么。",
        "自己做（Worker 看不到历史）",
    ),
]


async def main() -> None:
    container = await build_container()
    try:
        print("=" * 80)
        print("注册的 Agent:", ", ".join(container.registry.keys()))
        print("=" * 80)

        try:
            conv = await container.database.create_conversation("routing demo")
        except Exception as exc:  # noqa: BLE001
            print(f"\nCannot create a conversation: {exc}")
            print("(Is the backend running and holding the database? Stop it and retry.)")
            return

        delegated_count = 0
        for title, prompt, expectation in CASES:
            print(f"\n{'-' * 80}\n{title}\n  预期: {expectation}\n{'-' * 80}")
            print(f"  输入长度: {len(prompt)} 字符")

            msg_id = await container.database.add_message(conv.id, "user", prompt)
            execution = await container.orchestrator.start(
                conversation_id=conv.id, user_input=prompt, user_message_id=msg_id
            )
            outcome = await container.orchestrator.wait(execution.id)

            print(f"  结果: {outcome.status}")
            for step in outcome.steps:
                who = step.agent_label or step.agent_name
                marker = "   <-- 委派" if step.agent_name.startswith("local_") else ""
                print(f"    #{step.step_index}  {who:22s} {step.stage:12s} {step.status}{marker}")

            delegated = [s.agent_name for s in outcome.steps if s.agent_name.startswith("local_")]
            if delegated:
                delegated_count += 1
                print(f"  => 委派给 {', '.join(delegated)}")
            else:
                print("  => MainAgent 自己完成")

            totals = container.token_tracker.aggregate(execution.id)
            print(f"  Token: total={totals.total_tokens}  calls={totals.calls}")

        print(f"\n{'=' * 80}")
        print(f"4 个任务中有 {delegated_count} 个发生了委派 "
              f"（说明调度是动态判断，不是固定流水线）")
        print("=" * 80)
    finally:
        await shutdown_container(container)


if __name__ == "__main__":
    asyncio.run(main())
