# 多 Agent 开发指南（Developer Guide）

> 本指南里的每一步都在本项目里**实际跑通过**：`LocalCoderAgent` 就是一个按本文步骤
> 新增的、真实可用的 Agent，它的 17 个测试在 `backend/tests/test_new_agent.py`。

---

## 0. 先理解设计边界

系统里只有两类角色，职责不能混：

| | MainAgent（DeepSeek） | Worker（MiniCPM） |
| --- | --- | --- |
| 数量 | **只有一个** | 可以有任意多个 |
| 是否加载模型 | 否（调 API） | 否（**共享**一个 llama-server） |
| 输入 | 用户请求 + 完整上下文 + Agent State | **只有 Task + Context + Agent State** |
| 能否看到聊天历史 | 能（有窗口限制） | **不能，这是刻意的** |
| 能否继续委派 | 能 | **不能** |

**关键认知：加一个 Worker ≠ 加一个模型。** 所有 Worker 都通过
`LOCAL_MODEL_BASE_URL` 打到同一个 llama-server。一个 Worker 就是
「一份 System Prompt + 一组配置 + 一个输出约定」。

所以加 Agent 的成本极低 —— 这是架构要保证的事情。下面 6 步里有 4 步是改 1 行。

---

## 1. 完整示例：新增 `LocalCoderAgent`

这是**已经提交在仓库里**的真实实现，可以直接对照源码看。

### 步骤 1／6：写 Prompt 文件

新建 `backend/prompts/coder.md`。Prompt 就是 Agent 的灵魂，不需要写代码。

```markdown
# Local Coder Agent

You are the **Local Coder Agent**. You write one small, self-contained piece of
code from a precise specification, or you explain what is wrong with one.

## Rules

1. Produce the code the TASK asks for — nothing more.
2. Output a **single fenced code block**, then at most three short lines of prose.
3. Make it runnable as written: no placeholder imports, no `...`, no `# TODO`.
4. Prefer the standard library.
5. Never claim code was executed or tested — you have no runtime.
```

写 Prompt 的经验（从现有 5 个 Prompt 里总结）：

- **小模型需要「不要做什么」和「要做什么」一样明确。** MiniCPM5-2B 会热情地超范围发挥，
  所以每条 Prompt 都要有硬约束（"nothing more"、"no prose"、"output the JSON object
  and nothing else"）。
- **给出输出形状的例子。** 比描述更有效。
- **明确「信息不存在时怎么办」**（extractor 用 `null`，classifier 用 `null` 而不是硬套标签）。
- **不要在这里写系统级原则**（比如"你是多 Agent 系统的一员"）——那些在
  `_static_prefix.md` 里，只给 MainAgent 看。Worker 的 Prompt 要短、要专注。

### 步骤 2／6：新建 Agent 类（约 15 行，其中真正的逻辑为 0 行）

新建 `backend/agents/coder.py`：

```python
"""Local Coder Agent -- small, self-contained code generation and review."""

from __future__ import annotations

from agents.local_agent import LocalAgent


class CoderAgent(LocalAgent):
    key = "local_coder"                  # 决策/DB/UI 里的唯一标识，必须 local_ 前缀
    label = "LocalCoderAgent"            # UI 显示名
    role_description = "小段代码生成 / 代码缺陷检查 / 单元测试草稿"
    prompt_name = "coder"                # 对应 prompts/coder.md（不含扩展名）
    reasoning_field = "reasoning_coder"  # 对应 Settings 字段，见步骤 4
    output_kind = "text"                 # "json" 会自动尝试解析成 dict


__all__ = ["CoderAgent"]
```

**每个属性的作用：**

| 属性 | 必须？ | 说明 |
| --- | --- | --- |
| `key` | ✅ | 必须 `local_` 前缀。router 和 `is_local_agent_key()` 靠它判断是否需要本地端点 |
| `label` | ✅ | 时间线、Agent 面板显示的名字 |
| `role_description` | ✅ | Agent 面板里的一行说明（**用户可见，永不进入 Prompt**） |
| `prompt_name` | ✅ | 不含 `.md`。**写错会在运行时炸**，所以有测试守着 |
| `reasoning_field` | ✅ | Settings 字段名。**漏了 Agent 在 Settings 里就不可配置** |
| `output_kind` | 建议 | `json` 时结果会自动尝试解析，解析失败保留原文（不报错） |

> ⚠️ **不要覆写 `run()`。** 基类 `WorkerAgent.run()` 已经处理了
> Prompt 组装（Task → Context → Agent State 顺序）、usage 记录、
> reasoning 预算耗尽的自动重试、错误归一化。覆写它等于绕开这些保证。

### 步骤 3／6：注册（改 1 行）

`backend/orchestration/registry.py`：

```python
from agents.coder import CoderAgent          # ← 加这行

def build_workers(main_agent, local_provider):
    classes = (ExtractorAgent, SummarizerAgent, ClassifierAgent, ReviewerAgent,
               CoderAgent)                    # ← 加这个
    ...
```

**就这一步，Agent 已经可以被 MainAgent 委派了。** 剩下的都是「让它好看/可配」。

### 步骤 4／6：加 reasoning 配置（改 3 处）

`backend/core/config.py`：

```python
# Settings 类里
reasoning_coder: str = "low"        # none | low | medium | high

# Settings.reasoning_for() 里
"local_coder": self.reasoning_coder,

# RuntimeOverrides 类里（允许运行时改）
reasoning_coder: ReasoningEffort | None = None
```

**为什么默认 `low` 而不是 `none`?** 写代码需要一点思考。经验值：

| 任务类型 | 建议 | 理由 |
| --- | --- | --- |
| 抽取 / 分类 | `none` | 纯模式匹配，思考只会变慢且更容易跑偏 |
| 摘要 / 写码 | `low` | 需要一点结构规划 |
| 校验 / 审查 | `medium` | 需要对照多个维度，但要防止它改写内容 |

**未登记的 key 一律返回 `none`** —— 忘记配置的代价是「质量略低」，而不是「延迟暴涨」。

### 步骤 5／6：加到前端显示顺序（改 2 处）

`backend/services/runtime_state.py` 的 `AGENT_DISPLAY_ORDER`：
```python
"local_coder",      # ← 加
```

`frontend/src/lib/agents.ts` 的 `AGENT_ORDER` 和 `AGENT_META`：
```typescript
local_coder: {
  key: 'local_coder',
  label: 'LocalCoderAgent',
  shortLabel: 'Coder',
  model: 'MiniCPM5-2B',
  isLocal: true,
  role: '小段代码生成 / 代码缺陷检查',
},
```

同时 `STAGE_LABEL` 里加一个阶段名（`orchestrator.py` 的 `_stage_for()` 里有对应映射）：
```typescript
coding: 'Writing code…',
```

这两处**纯展示**，漏了不会崩，只是 UI 少一行。用 `AGENT_META` 兜底也不会报错
（`agentMeta()` 对未知 key 有 fallback）。

### 步骤 6／6：写测试（**不要跳过这一步**）

复制 `backend/tests/test_new_agent.py` 的 17 个测试作为模板。它们覆盖了一个新 Agent
**必须满足的全部契约**：

| 测试 | 防止什么 |
| --- | --- |
| `test_coder_is_registered` | 忘了注册 → Agent 永远调不到 |
| `test_new_agent_is_routable` | router 不认 → MainAgent 委派了但被降级 |
| `test_prompt_file_loads_and_is_non_empty` | `prompt_name` 拼错 → 运行时 FileNotFoundError |
| `test_prompt_name_resolves_for_every_worker` | 任一 Agent 的 Prompt 文件缺失 |
| `test_coder_runs_and_returns_output` | 基本能不能跑通 |
| `test_worker_prompt_has_task_context_and_state_tail` | **Prompt 顺序错** + **遥测泄漏进 Prompt** |
| `test_reasoning_defaults_are_safe_for_unmapped_keys` | 新 key 静默继承别人的策略 |
| `test_every_agent_declares_its_reasoning_settings_field` | 漏 `reasoning_field` → Settings 里配不了 |
| `test_settings_response_lists_every_registered_worker` | Settings 硬编码 → 新 Agent 在 UI 里消失 |
| `test_agent_display_order_covers_every_worker` | 忘了加显示顺序 |

**最后一个测试当场抓到了我自己的 bug**：我加完 `local_coder` 后，
`/api/settings` 里没有它 —— 因为 `api/settings.py` 当时是**硬编码**的 4 个 Agent 的字典。
已改成从 `registry.worker_keys()` 派生，并且现在有测试禁止再写死。

---

## 2. 验证清单

```powershell
# 1. 测试
conda activate MultiAgent
cd backend
python -m pytest tests/ -q                 # 现在 171 个

# 2. 重启后端（新 Agent 需要重启才注册）
# 3. 确认它出现在 API 里
Invoke-RestMethod http://127.0.0.1:8000/api/agents | Format-Table key,label,reasoning_effort,available

# 4. 确认 Settings 能看到它并能改
Invoke-RestMethod http://127.0.0.1:8000/api/settings | Select-Object -ExpandProperty local |
  Select-Object -ExpandProperty reasoning
```

第 3 步应该输出（实测）：

```
key              label                model         is_local reasoning_effort available
---              -----                -----         -------- ---------------- ---------
main             DeepSeek MainAgent   deepseek-chat    False medium                True
local_extractor  LocalExtractorAgent  MiniCPM5-2B       True none                  True
local_summarizer LocalSummarizerAgent MiniCPM5-2B       True low                   True
local_classifier LocalClassifierAgent MiniCPM5-2B       True none                  True
local_reviewer   LocalReviewerAgent   MiniCPM5-2B       True medium                True
local_coder      LocalCoderAgent      MiniCPM5-2B       True low                   True
```

---

## 3. 怎么让 MainAgent 真的用它

**这一步不需要改代码。** 你只要在步骤 3 把它注册进 registry，它的名字和职责就会
**自动出现在每次请求的 Prompt 里** —— 这一节解释这个机制，以及为什么它有时仍然不用。

### 机制：动态 Worker 目录

`_static_prefix.md`(静态前缀）里**故意不再列 Worker 清单**。清单由 registry 在每次请求时
生成，注入到 Prompt 最末尾：

```
===== INTERNAL AGENT STATE =====
user_goal: 从下面 6 条设备巡检记录里抽取...
available_agents: main, local_extractor, local_summarizer, local_classifier, local_reviewer, local_coder
local_workers_available: true
available_workers (delegate only to these):
  - local_extractor: LocalExtractorAgent — 信息抽取 / 实体与字段提取 / 文本转 JSON
  - local_summarizer: LocalSummarizerAgent — 摘要 / 长文本压缩 / 上下文精简
  - local_classifier: LocalClassifierAgent — 文本分类 / 意图识别 / 任务类型与标签判定
  - local_reviewer: LocalReviewerAgent — 格式校验 / 完整性检查 / 明显错误与一致性核查
  - local_coder: LocalCoderAgent — 小段代码生成 / 代码缺陷检查 / 单元测试草稿
steps_remaining: 12
```

为什么这样设计 —— 这是开发时**踩过的真实坑**：

> 我加完 `local_coder` 后，它注册了、router 也认，但 MainAgent **从来不用它**。
> 原因：静态前缀里写死了 4 个 Worker 的表格，MainAgent 从没被告知第 5 个存在。
> 把清单改成动态生成后，**注册一个新 Agent 就够了，不用碰任何 Prompt**。
>
> 现在有测试禁止再写死（`test_static_prefix_no_longer_hardcodes_worker_keys`，
> 以及 `test_new_agent_reaches_the_prompt_without_editing_prompts`）。

额外好处：**本地模型离线时清单会自动变空**，MainAgent 根本不会尝试委派一个连不上的
Agent（`worker_catalog(local_available=False)` 返回 `{}`，渲染为
`available_workers: (none — complete the request yourself)`）。

### 但是：注册 ≠ 一定会被用

MainAgent 会**判断委派是否划算**，这符合它的设计原则（"不要为了调用 Agent 而调用 Agent"）。
实测同一个系统的四种输入：

| 输入 | 字符数 | 实际行为 | 为什么 |
| --- | --- | --- | --- |
| 用 Python 写个回文判断函数，只给代码 | 30 | **自己做** | 3 行代码，委派的往返开销（本地 ~2.6s）比直接写更大 |
| 6 条巡检记录批量抽取三个字段成 JSON | 422 | **委派给 local_extractor** | 重复的机械劳动，MainAgent 的理由是"机械式字段抽取，6 条记录结构一致，适合本地抽取器" |
| 证明 n³−n 能被 6 整除 | 28 | **自己做** | 2B 模型做不了多步证明 |
| 概括我们刚才聊的 + 判断我下一步该做什么 | 35 | **自己做** | Worker 看不到对话历史，也没有判断力 |

**结论：多 Agent 不是固定流水线。** 委派只在"机械、自包含、可校验、且省 Token"时才发生。
短任务不被委派是**正确行为**，不是 bug。

想让某个任务稳定地被委派，把它写得**更像 Worker 的活**：

```
❌ 帮我处理一下这些数据
❌ 解释一下这段代码
✅ 从下面 20 条记录里抽取 A、B、C 三个字段，输出 JSON 数组，每条记录一个对象
✅ 把这 3000 字压缩成 3 句话，保留所有人名和数字
✅ 判断下面这段文本属于"投诉 / 咨询 / 表扬"中的哪一类
```

### 自己验证调度

本仓库带了一个可直接运行的调度演示：

```powershell
cd backend
python scripts\demo_routing.py       # 4 种任务，打印实际执行链 + Token
python scripts\inspect_decision.py   # 打印 MainAgent 看到的完整 Prompt 和它的决策理由
```

`inspect_decision.py` 特别有用：它把注入的 Worker 目录、Prompt 结构、
以及 MainAgent 自述的 `reason` 全部打出来，是排查"为什么没委派"的最快路径。

---

## 3.1 如果确认该委派却没委派，按这个顺序查

| 排查项 | 怎么查 | 常见原因 |
| --- | --- | --- |
| Agent 注册了吗 | `Invoke-RestMethod http://127.0.0.1:8000/api/agents` | 忘了加进 `build_workers` 的 `classes` |
| 本地模型在线吗 | `.\scripts\status.ps1` | llama-server 没起，目录自动为空，必然不委派 |
| 它在 Prompt 里吗 | `python scripts\inspect_decision.py` | `available_workers` 里没有 → 检查 `role_description` 是否为空 |
| 职责描述清楚吗 | 同上，看目录里的那行文字 | 描述太笼统（"处理文本"）→ MainAgent 不知道该在何时用 |
| 任务本身适合委派吗 | 看上面的对照表 | 任务太短/需要推理/需要历史 → **不委派是正确行为** |
| 被路由拦下了吗 | 开 Debug Mode 看时间线的 `decision` 事件 | `no budget left` / `worker offline` 会记在 Agent State 的 `errors` 里 |



## 4. 其他扩展点

### 换/加模型供应商

`providers/base.py` 已经定义了统一接口：

```python
class LLMProvider(abc.ABC):
    async def generate(self, messages, options) -> LLMResponse: ...
    def stream(self, messages, options) -> AsyncIterator[tuple[str, str | None]]: ...
    async def health_check(self) -> dict: ...
```

新增 `providers/ollama.py` 或 `providers/openai.py` 只需实现这 3 个方法，
然后在 `api/container.py` 里换掉构造，或给 `AgentRegistry` 传不同的 provider。
**Agent 层完全不用改。**

### 让 Worker 并行执行

`AgentDecision.parallel` 字段已预留。真正要用需要：

1. `AgentDecision` 增加 `tasks: list[AgentTask]`
2. orchestrator 的 worker 分支改成 `asyncio.gather`
3. **前端 `reduceEvent` 要改成按 `step_index` 排序**（目前是按事件到达顺序 append）

前置条件：本地 4 个 slot 已经支持并发，但注意 `--ctx-size` 是**所有 slot 共享**的
（见 `docs/LIMITATIONS.md` 第 4 节）。

### 给 Worker 加工具调用

当前 Worker 是纯文本进、纯文本出。要加工具（比如让 extractor 真的去查数据库）
需要改 `WorkerAgent.run()`，在 Prompt 里描述工具、解析调用、执行、把结果回灌。
**建议先不要做** —— 2B 模型的工具调用可靠性很低，会让 MainAgent 的校验负担变得很重。
更划算的做法是让 MainAgent 做工具调用，Worker 只做纯文本变换。

---

## 5. 常见坑

| 坑 | 症状 | 原因 / 解决 |
| --- | --- | --- |
| `key` 没加 `local_` 前缀 | Agent 在 router 里被当成云端 Agent | `is_local_agent_key()` 靠前缀判断；前缀错了会去用 DeepSeek provider |
| `prompt_name` 拼错 | 运行时 `PromptNotFoundError` | 有测试守着（`test_prompt_name_resolves_for_every_worker`） |
| 忘写 `reasoning_field` | Agent 能用，但 Settings 里改不了它的 reasoning | 有测试守着 |
| 忘了加 `AGENT_DISPLAY_ORDER` | Agent 能跑，但左栏不显示 | 有测试守着 |
| 覆写了 `run()` | Prompt 顺序错、遥测可能泄漏进 Prompt | **不要覆写**；要改行为就改 Prompt |
| 在 Prompt 里塞 token/延迟 | 违反项目核心约束 | `prompt_builder` 会抛 `TelemetryLeakError` |
| 把聊天历史传给 Worker | Token 暴涨、小模型跑偏 | Worker 只该收到 MainAgent 提炼过的 Task + Context |
| 新 Agent 想「顺便」多做点事 | 输出不稳定、MainAgent 难以校验 | Prompt 里写死"nothing more"，一个 Agent 只做一件事 |

---

## 6. 三条设计原则

加 Agent 之前先问自己：

1. **这个任务是不是机械的、可验证的、自包含的？**
   如果它需要多步推理、需要世界知识、需要看历史 —— 它属于 MainAgent，不属于 Worker。
   把难任务塞给小模型是最常见的架构错误。

2. **这个 Agent 和现有的有什么本质区别？**
   如果只是 Prompt 里一句话不同，考虑改现有 Prompt 而不是新增 Agent。
   Agent 多了，MainAgent 的选择负担和错误率都会上升。

3. **我能不能在 Prompt 里把「输出形状」写清楚？**
   写不清楚就说明任务边界模糊，MainAgent 也无法可靠地校验结果。
   先想清楚输出，再建 Agent。
