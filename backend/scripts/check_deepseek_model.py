"""Which DeepSeek model serves the MainAgent, and how is its reasoning controlled?

Runs against the live API. Reports, for the configured model and each model the
account exposes:

  * the `model` field the API returns (authoritative: what actually served it)
  * whether the model thinks natively (reasoning_content / reasoning_tokens)
  * whether it accepts `reasoning_effort`, as discovered by the provider probe
  * measured cost and latency for one identical question

    python scripts/check_deepseek_model.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from core.config import effective_settings  # noqa: E402
from providers.base import GenerateOptions, LLMMessage  # noqa: E402
from providers.deepseek import DeepSeekProvider  # noqa: E402

QUESTION = "一个笼子里有鸡和兔共 35 只，脚共 94 只。鸡和兔各多少只？只给最终数字。"


async def probe(provider: DeepSeekProvider, model: str) -> None:
    print(f"\n{'-' * 76}\n{model}\n{'-' * 76}")
    provider.set_model(model)

    # Learn whether this model takes reasoning_effort (cached afterwards).
    supports_effort = await provider.detect_reasoning_support()
    print(f"  reasoning 模式      : {provider.reasoning_mode}")
    print(f"  接受 reasoning_effort: {supports_effort}")

    started = time.perf_counter()
    try:
        response = await provider.generate(
            [LLMMessage(role="user", content=QUESTION)],
            GenerateOptions(max_tokens=1024, temperature=0.0, reasoning_effort="medium"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {type(exc).__name__}: {str(exc)[:160]}")
        return
    elapsed = (time.perf_counter() - started) * 1000

    usage = response.usage
    print(f"  服务端回报 model    : {response.model!r}")
    print(f"  content             : {(response.content or '(空)')[:60]!r}")
    print(f"  reasoning_content   : {'有 ' + str(len(response.reasoning)) + ' 字符' if response.reasoning else '无'}")
    print(
        f"  tokens              : prompt={usage.prompt_tokens} completion={usage.completion_tokens} "
        f"reasoning={usage.reasoning_tokens} total={usage.total_tokens}"
    )
    print(f"  实测延迟            : {elapsed:.0f} ms")


async def main() -> None:
    settings = effective_settings()
    print("=" * 76)
    print("当前生效配置")
    print("=" * 76)
    print(f"  DEEPSEEK_BASE_URL : {settings.deepseek_base_url}")
    print(f"  DEEPSEEK_MODEL    : {settings.deepseek_model}   <-- MainAgent 使用它")
    print(f"  REASONING_MAIN    : {settings.reasoning_main}")

    provider = DeepSeekProvider(settings)
    try:
        health = await provider.health_check()
        available = health.get("models") or []
        print(f"  账号可用模型      : {available}")

        await probe(provider, settings.deepseek_model)
        for candidate in available:
            if candidate and candidate != settings.deepseek_model:
                await probe(provider, candidate)
    finally:
        await provider.aclose()

    print(f"\n{'=' * 76}")
    print("判读")
    print("=" * 76)
    print("  * `model` 字段由服务端回报，即实际服务该请求的模型")
    print("  * reasoning 模式:")
    print("      model-native      -> 模型自己就会思考，不应再发 reasoning_effort")
    print("      effort-controlled -> 思考由 reasoning_effort 控制（不发 = 不思考）")
    print("  * 同一个模型名，发不发 reasoning_effort 行为完全不同，")
    print("    所以必须探测：靠模型名猜，换账号后会静默失效。")
    print("  * 换模型只改 .env 的 DEEPSEEK_MODEL，不需要改代码。")


if __name__ == "__main__":
    asyncio.run(main())
