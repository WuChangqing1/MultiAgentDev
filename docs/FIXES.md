# 已修复缺陷说明（Defect Log）

本文档记录开发过程中实测发现的 7 个真实缺陷、它们的**根因**、**修复位置**与**回归测试**。
所有缺陷都是端到端跑真实模型时暴露出来的，不是静态审查发现的。

> 回归验证：`cd backend; python -m pytest tests/ -q` → 154 passed

---

## 1. 步骤索引双重计数，导致步骤预算对不上

**症状**：`MAX_AGENT_STEPS=12` 时，Timeline 里 `main` 和 `local_extractor` 共用同一个
`step_index`（都是 `#1`、`#2`…），循环实际跑了 13 次而不是 6 次；预算形同虚设，
而且「留给 finalize 的槽位」永远被规划阶段吃光。

**根因**：`_step_loop` 里有一个本地计数器 `step_index`，同时 `ExecutionStateManager`
里也有一个 `state.step_index`，两者各自 `+= 1`。主 Agent 调用与 Worker 调用都读同一个
`step_index`，于是同一个索引被两个 timeline 条目复用，而循环条件判断的是另一个计数器。

**修复**：把「步骤编号」和「预算」统一成**唯一计数器**，只在
`ExecutionStateManager.reserve_index()` 一个地方自增。

- `backend/orchestration/state_manager.py:69` — `reserve_index()`（每次 timeline 条目调用一次）
- `backend/orchestration/state_manager.py:82` — `planning_exhausted` 属性
- `backend/orchestration/orchestrator.py:287` — `while not state.planning_exhausted:`
- `backend/orchestration/orchestrator.py:288,374,453,492` — 四处 `state.reserve_index()`

**要点**：`planning_budget` 与 `step_index` 分离 —— 规划阶段受 `max_steps` 约束，
而 finalize 阶梯被**故意允许**越过预算（它不能委派，必然终止）。宁可多花一两次调用，
也不要让一次已经跑了 12 步的任务以失败告终。

**回归测试**：`tests/test_orchestrator.py::test_step_budget_forces_a_final_answer`
断言 `indices == list(range(1, len(indices) + 1))`（索引唯一且连续）。

---

## 2. finalize 阶梯从未真正执行

**症状**：一个只会 `delegate` 的模型，在步骤预算耗尽后系统直接返回
`failed: Step budget exhausted`，用户什么都拿不到 —— 尽管代码里写了一个三级的
`_FINALIZE_LADDER`。

**根因**（两层，第二层更隐蔽）：

1. 规划阶段先把所有槽位用光，轮到 finalize 时已经没有预算了。
2. **第三级阶梯写的是「请不要用 JSON，直接输出纯文本」，但代码仍然走
   `_run_main_turn()` 的 JSON 解析路径**。模型确实听话地输出了纯文本 Markdown，
   但被 `parse_decision` 当作「解析失败」降级，最终 `answer` 为 `None`，
   于是这一级等于没写。

**修复**：

- `backend/orchestration/orchestrator.py:444` — 只对**前两级**使用 JSON 路径
  （`_FINALIZE_LADDER[:-1]`）
- `backend/orchestration/orchestrator.py:481` — 新增 `_plain_text_final_answer()`，
  最后一级**绕开 JSON 协议**，直接调用 `main.stream_answer()` 流式拿到纯文本
- `backend/orchestration/orchestrator.py:1113` — `_FINALIZE_LADDER` 三级指令

**回归测试**：`tests/test_orchestrator.py` 用
`AlwaysDelegatingProvider`（按 prompt 内容决定返回什么，而不是按调用序号）
验证「要求纯文本时真的得到纯文本」，且 `provider.streams_seen == 1`。

> 设计教训：**用调用序号硬编码的假 provider 是不可靠的测试**。改成按 prompt 语义响应后，
> 测试才真正描述了契约（"如果你要纯文本，就该拿到纯文本"），也才暴露出上面第 2 层根因。

---

## 3. 运行时设置改动没传导到已构造的 provider（界面显示新值，请求仍打旧地址）

**症状**：在 Settings 面板把 `Local Model URL` 改成别的地址、点「测试连接」显示失败，
但后续请求**依然发往旧地址**，好像设置没生效。降级测试因此假通过。

**根因**：`Settings` 是 Pydantic 模型，`SettingsStore.effective()` 原本用
`model_copy(update=...)` 返回**副本**。而 provider / agent 在构造时就持有了
**同一个 Settings 实例的引用**。结果是：API 返回了新值（界面正确），
但所有长生命周期对象仍在读老对象 —— 这是一个典型的「改了副本」bug。

**修复**（三处配合）：

- `backend/core/config.py` — `SettingsStore.effective()` 改为**原地修改** `self._base`
  （`setattr`），并新增 `_env_baseline` 快照供 `reset()` 还原
- `backend/providers/llama_cpp.py:146` — 新增 `refresh()`：endpoint / key / timeout
  变化时丢弃缓存的 HTTP client
- `backend/providers/deepseek.py:127` — 同样的 `refresh()`
- `backend/api/container.py:51` — `refresh_settings()` 统一调用
  `deepseek.set_model()` / `deepseek.refresh()` / `local.refresh()` /
  `model_health.invalidate()` / 每个 `agent.update_settings()`

**回归测试**：`tests/test_settings_runtime.py`（6 个）
- `test_overrides_are_applied_in_place` 断言 `effective() is original`
- `test_local_provider_refresh_drops_stale_client` 断言换地址后拿到**不同的** client 对象
- 实测降级脚本 `scripts/verify_fallback.py` 现在能真正把本地端点打到死端口

---

## 4. `api/settings.py` 缺少 import，导致整个应用启动失败

**症状**：`uvicorn main:app` 直接崩：

```
NameError: name 'ModelStatus' is not defined
  at @router.post("/validate", response_model=list[ModelStatus])
```

**根因**：一次重构中删掉了 `from models.schemas import ModelStatus`，但这个类型只用在
**装饰器**里。装饰器在模块导入时求值，所以整个 `main` 模块无法导入 ——
应用完全起不来。而当时的测试套件**没有任何测试导入 `main`**，因此全部测试是绿的。

**修复**：

- `backend/api/settings.py:14` — 补回 `from models.schemas import ModelStatus`
- `backend/tests/test_imports.py` — **新增 43 个测试**，其中：
  - `test_module_imports_cleanly` 参数化导入全部 37 个模块（含 `main`）
  - `test_app_registers_every_route` 从 OpenAPI schema 断言 20 条路由齐全

**关键教训**：只会「用装饰器里的名字」的缺失导入，对单元测试是不可见的。
**必须有一个测试去导入真正的入口模块。** 这个测试现在永久存在。

---

## 5. 步骤 Token 重新打开会话后丢失

**症状**：实时执行时 Timeline 每个步骤都有 Token 数字；一旦刷新页面 / 打开历史会话，
所有步骤的 Token 变成 `—`。

**根因**：Token 数据存在 `model_calls` 表（一行一次调用，带 `agent_step_id` 外键），
但 `Database.get_execution()` 只用 `AgentStepRow` 组装 `AgentStep`，
**从来没去读 `model_calls`**。实时视图走的是内存里的 `TokenTracker`，所以有数字；
历史视图走数据库，所以没有。

**修复**：`backend/db/database.py:309` 起

- `usage_by_step` 按 `agent_step_id` 把 `model_calls` 分组
- `_call_to_usage()`（`:580`）把一行还原成 `TokenUsage`
- `_combine_usages()`（`:602`）把同一步的多次调用（例如重试）**求和**，而不是只留最后一次
- 重试次数、`estimated` 标注也一并还原

**回归测试**：`tests/test_database.py::test_step_usage_is_restored_from_model_calls`
（两次调用 → 断言 prompt=150、completion=30、latency=1000，即求和而非覆盖）

---

## 6. `NO_PROXY` 里的 `[::1]` 让所有 HTTP 请求崩溃

**症状**：健康检查、模型调用、甚至 `httpx.Client(...)` 构造本身全部抛异常：

```
httpx.InvalidURL: Invalid port: ':1]'
```

**根因**：Windows 上 `NO_PROXY` 常见写法是
`...,localhost,127.0.0.1,::1,[::1]`。httpx 会把 `NO_PROXY` 按逗号切开后
**每个条目当 URL 解析**；`[::1]` 被解析成 host=`[` + port=`:1]`，
于是 **构造 client 的那一刻就崩**，请求根本发不出去。这是环境问题，但必须在代码里兜住。

**修复**：新增 `backend/core/net.py`

- `_normalize_token()` 去掉会导致解析失败的方括号形式，同时把可用的 `::1` 加回来
- `_ALWAYS_EXEMPT` 恒定豁免 `localhost / 127.0.0.1 / ::1 / 0.0.0.0`
- **`backend/core/net.py:98` 在模块导入时立即执行 `sanitize_no_proxy()`** ——
  这是关键：HTTP client 在**构造时**读取该变量，晚一步调用就已经太迟了
- `backend/core/config.py` 顶部 `from core import net` 保证只要加载配置就已生效

**回归测试**：`tests/test_net.py`（8 个），其中
`test_httpx_client_can_be_constructed` 直接复现崩溃场景。

> 排查提示：如果将来在别的地方遇到同样报错，手动
> `$env:NO_PROXY = "localhost,127.0.0.1,::1"` 即可临时绕过。

---

## 7. `StatsResponse` 类型错误导致 `/api/stats` 返回 500

**症状**：前端 Session 面板空白，后端日志：

```
ValidationError: by_agent.main
  Input should be a valid dictionary or instance of TokenUsage
  [input_type=TokenUsageAggregate]
```

**根因**：`StatsResponse.by_agent` 声明为 `dict[str, TokenUsage]`，
但 `Database.aggregate_stats()` 返回的是**聚合结果** `TokenUsageAggregate`。
只有一个调用的 agent 能通过（结构恰好兼容），多个调用就 500 ——
所以必须在**跑过多次调用之后**才会暴露。API 测试当时查的是空的 stats，因此没抓到。

**修复**：

- `backend/models/schemas.py:332` —
  `by_agent: dict[str, TokenUsageAggregate]`
- `backend/tests/test_database.py::test_aggregate_stats_feeds_the_stats_schema` —
  断言 `aggregate_stats()` 的输出**可以直接构造** `StatsResponse`

**教训**：测一个聚合接口，必须在**聚合非空**的状态下测。

---

## 修复对照表

| # | 缺陷 | 修复位置 | 回归测试 |
| --- | --- | --- | --- |
| 1 | 步骤索引双重计数 | `orchestration/state_manager.py:69,82`、`orchestrator.py:287` | `test_orchestrator.py` |
| 2 | finalize 阶梯未执行 | `orchestrator.py:444,481,1113` | `test_orchestrator.py` |
| 3 | 设置改动未传导 | `core/config.py`、`providers/*.py:146,127`、`api/container.py:51` | `test_settings_runtime.py` |
| 4 | 缺 import 导致启动失败 | `api/settings.py:14` | `test_imports.py`（43 个） |
| 5 | 步骤 Token 丢失 | `db/database.py:309,580,602` | `test_database.py` |
| 6 | `[::1]` 导致 HTTP 崩溃 | `core/net.py:98`（导入时执行） | `test_net.py`（8 个） |
| 7 | `/api/stats` 500 | `models/schemas.py:332` | `test_database.py` |

## 从这批缺陷中学到的通用规则

1. **只有一个地方能自增计数器。** 两个计数器表示同一个东西时，它们一定会不同步。
2. **测试替身不要按调用序号响应，要按输入语义响应。** 否则测试会掩盖真实缺陷（缺陷 2）。
3. **配置对象要么全不可变，要么原地改。** 「返回修改后的副本」是最危险的中间态（缺陷 3）。
4. **必须有一个测试导入真正的应用入口。** 装饰器里的名字对单元测试是隐形的（缺陷 4）。
5. **写进数据库的东西，必须有读取路径被测试覆盖。** 只写不读等于没存（缺陷 5）。
6. **环境相关的崩溃要在导入期兜住**，不能等到第一次请求（缺陷 6）。
7. **聚合接口要在非空状态下测。** 空集合能通过任何类型检查（缺陷 7）。
