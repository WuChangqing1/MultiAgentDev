# MultiAgent — DeepSeek MainAgent × 本地 MiniCPM5-2B

一个**本地优先**的多 Agent 协同系统。DeepSeek 作为主脑负责规划、路由、校验与最终裁决；
本地 llama.cpp 上的 MiniCPM5-2B 作为 Worker，承担抽取、摘要、分类、校验等机械性任务。
FastAPI 作为 Orchestrator，React 前端实时展示「现在谁在工作」以及每一步的 Token 与耗时。

核心信条：

```
Strong Model Thinks.
Small Model Works.
Orchestrator Coordinates.
Frontend Observes.
```

配套的数据隔离原则（本项目在**代码结构层面**强制，而不是靠开发者记忆）：

```
Context      →  Prompt 正文
Agent State  →  Prompt 最末尾（===== INTERNAL AGENT STATE =====）
Telemetry    →  前端 / 数据库 / 日志        （永不进入 Prompt）
```

---

## 目录

- [1. 它是什么](#1-它是什么)
- [2. 架构](#2-架构)
- [3. 环境要求](#3-环境要求)
- [4. 快速开始](#4-快速开始)
- [5. 配置 `.env`](#5-配置-env)
- [6. 启动 llama.cpp](#6-启动-llamacpp)
- [7. 启动后端](#7-启动后端)
- [8. 启动前端](#8-启动前端)
- [9. 访问页面](#9-访问页面)
- [10. Agent 架构](#10-agent-架构)
- [11. 通信协议](#11-通信协议)
- [12. 状态与遥测的分离](#12-状态与遥测的分离)
- [13. Token 统计的真实性](#13-token-统计的真实性)
- [14. 容错与降级](#14-容错与降级)
- [15. 目录结构](#15-目录结构)
- [16. 后端 API](#16-后端-api)
- [17. 数据库](#17-数据库)
- [18. 测试与验证](#18-测试与验证)
- [19. 常见问题](#19-常见问题)
- [20. 已知限制与后续扩展](#20-已知限制与后续扩展)

### 专项文档

| 文档 | 内容 |
| --- | --- |
| **[docs/STARTUP.md](docs/STARTUP.md)** | **API Key 写在哪 · 重启后怎么启动 · 怎么确认一切正常** |
| **[docs/DEVELOPING.md](docs/DEVELOPING.md)** | **怎么开发新的多 Agent（6 步，含真实示例 `LocalCoderAgent`）** |
| [docs/FIXES.md](docs/FIXES.md) | 开发中实测发现的 7 个缺陷：根因、修复位置、回归测试 |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | 6 个已知限制的逐条优化方案与判断标准 |

---

## 1. 它是什么

用户提问后，系统不会无脑地把问题丢给一个模型，而是：

1. **DeepSeek MainAgent** 判断这个请求该怎么完成 —— 自己做，还是拆给本地小模型。
2. Orchestrator 按决策调度 **Local Worker**（Extractor / Summarizer / Classifier / Reviewer）。
3. Worker 结果回灌给 MainAgent，它继续推理、必要时重新委派或修正。
4. MainAgent 输出最终回答。

同时，前端把整个执行过程**实时**呈现出来：当前在跑哪个 Agent、用哪个模型、每个步骤的
Token / 延迟 / 吞吐、以及可折叠的 reasoning。

工作负载是**动态**的，不是固定流水线：

- 简单抽取 → 委派给 Extractor
- 复杂证明 → MainAgent 自己完成，一次委派都不发生
- 本地模型离线 → 自动降级为 DeepSeek 单独完成，并明确告知用户

## 2. 架构

```text
                        用户
                         ↓
              React + Vite 前端 (127.0.0.1:5173)
                         ↓  /api  (Vite dev proxy)
              FastAPI Backend / Orchestrator (127.0.0.1:8000)
                         ↓
                 DeepSeek MainAgent
              Planner · Router · Reviewer
                         ↓  根据任务动态决定
   ┌─────────────┬──────────────┬──────────────┬─────────────┐
   │  自己做      │ Extractor    │ Summarizer   │ Classifier  │
   │ (DeepSeek)  │  ┐           │  ┐           │  ┐          │
   └─────────────┴──┴───────────┴──┴───────────┴──┴──────────┘
                    同一份 MiniCPM5-2B（共享一个 llama-server）
                         ↓
                 结果汇总 → MainAgent 整理
                         ↓
                       用户
```

**关键点：四个 Worker 不是四个模型实例。** 它们共享同一个
`http://127.0.0.1:8080/v1`，区别只在 System Prompt、任务、reasoning 策略。

## 3. 环境要求

| 组件 | 版本 / 说明 |
| --- | --- |
| Conda 环境 | `MultiAgent`（**复用现有环境，不要新建**） |
| Python | 3.12（与现有环境一致） |
| Node.js | 18+（本项目在 Node 22 上验证） |
| llama.cpp | `llama-server.exe`，已在 `127.0.0.1:8080` 提供 OpenAI 兼容 API |
| 本地模型 | `MiniCPM5-2B-Q8_0.gguf`（已部署，无需重新下载） |
| DeepSeek | 一个 API Key（https://platform.deepseek.com/） |

## 4. 快速开始

```powershell
# 0) 进入项目目录
cd D:\CodingData\Github\dsh\MultAgentDev

# 1) 激活已有的 conda 环境
conda activate MultiAgent

# 2) 安装后端依赖
python -m pip install -r backend\requirements.txt

# 3) 安装前端依赖
cd frontend
npm install
cd ..

# 4) 配置 .env（填入 DEEPSEEK_API_KEY，这是唯一需要填的地方）
Copy-Item .env.example .env
notepad .env

# 5) 确认 llama-server 在 8080 运行（已运行则脚本不会打扰它）
.\scripts\start_llama_server.ps1

# 6) 一键启动后端 + 前端
.\start.ps1
```

浏览器打开 **http://127.0.0.1:5173**。

> **系统重启之后**同样是执行第 5、6 步（或只执行 `.\start.ps1`，它会自动探测并提示）。
> 随时可以用 `.\scripts\status.ps1` 一条命令查看所有组件状态。
> 详见 **[docs/STARTUP.md](docs/STARTUP.md)**。

> 也可以分两个终端手动启动，见 [第 7 节](#7-启动后端) 与 [第 8 节](#8-启动前端)。

## 5. 配置 `.env`

`.env` 位于项目根目录，**已被 `.gitignore` 忽略**，绝不会被提交。

```env
# DeepSeek —— Main Agent
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# 本地 MiniCPM
LOCAL_MODEL_BASE_URL=http://127.0.0.1:8080/v1
LOCAL_MODEL_NAME=MiniCPM5-2B

# 调度
MAX_AGENT_STEPS=12
```

完整可配置项见 [`.env.example`](.env.example)（每一项都有注释）。
要点：

- **API Key 只从 `.env` 读取**，代码中没有任何硬编码，前端也不会拿到明文
  （Settings 面板只显示 `sk-5***…***e63` 这样的指纹）。
- 前端**从不**直接调用 DeepSeek 或 llama.cpp：所有流量都经过 FastAPI。
- 运行时可以改的项（模型名、endpoint、max_tokens、温度、reasoning 策略、
  步骤上限、是否启用本地 Worker）可以从右上角 **Settings** 修改，只影响当前进程；
  重启后回到 `.env` 的值。

## 6. 启动 llama.cpp

模型与 `llama-server.exe` 已经就位，脚本只负责启动，**不会启动或关闭你自己开着的
llama-server**（若 8080 已在服务，脚本直接退出）：

```powershell
.\scripts\start_llama_server.ps1
```

等价的手动命令：

```powershell
& "D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp\llama-server.exe" `
  -m "D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp\models\MiniCPM5-2B\MiniCPM5-2B-Q8_0.gguf" `
  --alias MiniCPM5-2B --host 127.0.0.1 --port 8080 --ctx-size 16384 --n-gpu-layers 99 --jinja
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

## 7. 启动后端

```powershell
conda activate MultiAgent
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

或者用脚本（会自动定位 conda 环境、检查依赖与 `.env`、探测本地模型）：

```powershell
.\scripts\start_backend.ps1
```

| 地址 | 用途 |
| --- | --- |
| http://127.0.0.1:8000/docs | Swagger UI |
| http://127.0.0.1:8000/api/health | 健康检查 |

> 后端默认只绑定 `127.0.0.1`，这是本地工具，**不要**改成 `0.0.0.0`。

## 8. 启动前端

```powershell
cd frontend
npm run dev
```

或：

```powershell
.\scripts\start_frontend.ps1
```

前端默认 http://127.0.0.1:5173 ，通过 Vite 代理把 `/api` 转发到后端 8000。

## 9. 访问页面

打开 http://127.0.0.1:5173 ，界面分三栏：

| 区域 | 内容 |
| --- | --- |
| **左栏** | Agent 列表（含实时状态圆点：Idle / Queued / Running / Thinking / Completed / Failed）+ 模型在线状态 |
| **中栏** | 对话区、当前 Agent 横幅、Markdown 回答（代码高亮 + 复制）、输入框（Enter 发送 / Shift+Enter 换行） |
| **右栏** | Execution 面板，四个页签：**Timeline**（可点击的执行时间线）、**Flow**（Agent 流程图）、**Tokens**（Token 面板）、**Session**（会话累计统计） |

顶部还有一个「**Current Agent**」横幅，实时显示现在是谁在工作、用哪个模型、在做什么
（例如 `LocalExtractorAgent · Extracting structured information…`）。

右上角按钮：新建会话、**Debug Mode**、显示/隐藏右栏、明暗主题、Settings。

### 三个内置演示

点击空状态里的示例即可填入输入框：

| 演示 | 输入 | 预期行为 |
| --- | --- | --- |
| 提取 + 摘要 | 「请分析下面这段内容，提取关键信息并总结。张三今年20岁…」 | MainAgent → LocalExtractor → MainAgent |
| 复杂推理 | 「证明：对任意正整数 n，n³ − n 能被 6 整除」 | MainAgent **独立完成**，不委派 |
| 结构化抽取 | 会议记录 → JSON | MainAgent → Extractor →（可能 Reviewer）→ MainAgent |

## 10. Agent 架构

### MainAgent（DeepSeek）

理解请求、任务规划与拆解、判断是否委派、选择 Worker、组织执行顺序、基于 Worker 结果继续
推理、必要时重新规划、判断完成度、生成最终回答。**最终裁决权始终在它手里。**

### Local Workers（MiniCPM5-2B，共享同一份模型）

| Agent key | 职责 | 默认 reasoning |
| --- | --- | --- |
| `local_extractor` | 信息抽取、实体/字段提取、文本 → JSON | `none` |
| `local_summarizer` | 摘要、长文本压缩、上下文精简 | `low` |
| `local_classifier` | 文本分类、意图判断、标签判定 | `none` |
| `local_reviewer` | 格式校验、完整性检查、一致性核查 | `medium` |

四者是**同一 endpoint 上的四套 Prompt / 配置**，由统一基类 `LocalAgent` 承载调用逻辑
（`backend/agents/local_agent.py`），不存在四份模型实例。

### Reasoning 策略

在 `.env` 中按 Agent 配置 `none | low | medium | high`，运行时也可在 Settings 里改。
实现上通过 llama.cpp 的 `chat_template_kwargs`（`enable_thinking` / `reasoning_budget`）下发；
**如果某个 llama.cpp 构建不支持该参数，客户端会自动去掉它并重试，绝不会因此让请求失败。**

> 实测经验：MiniCPM5-2B 在 reasoning_budget 极小时会把预算全部花在思考上、最终
> `content` 为空。因此 `low` 使用 512 而不是 256，且 Worker 检测到「有 reasoning 但
> 无 content」时会自动用更大预算重试一次。若仍为空，会把 reasoning 明确标注为
> 「非最终答案」交回 MainAgent，而不是给一个空字符串。

## 11. 通信协议

MainAgent 每一步返回**一个 JSON 对象**：

```json
{
  "action": "delegate",
  "agent": "local_extractor",
  "task": "Extract name, age and major. Return JSON.",
  "context": "张三今年20岁，是软件工程专业学生。",
  "expected_format": "json",
  "reason": "Mechanical field extraction."
}
```

支持的 `action`：`delegate` / `answer` / `continue` / `review` / `replan`。

解析器（`backend/orchestration/decision_parser.py`）**永远不会抛异常**，逐级降级：

1. 直接 `json.loads`
2. 提取 ```json 代码块
3. 括号配平扫描（容忍前后夹带解释文字）
4. 补全被 `max_tokens` 截断的 JSON
5. 机械修复：字符串内裸换行、单引号、尾随逗号、未加引号的 key、`//` 注释、Python 字面量
6. **协议漂移兜底**：即使模型自创字段名（`worker` / `instruction` / `plan: [...]`），
   也会被还原成一个合法决策
7. 全部失败 → 降级为「把原文当作回答」，任务继续而不是崩溃

每一次尝试都记录在 timeline 的 Debug 信息里。

## 12. 状态与遥测的分离

这是本项目最重要的结构约束，它在**代码层面**被强制，而不是靠约定：

| 层 | 含义 | 去向 |
| --- | --- | --- |
| **Context** | 模型完成任务真正需要知道的信息 | Prompt 正文 |
| **Agent State** | 模型完成当前流程真正需要知道的运行状态 | **Prompt 最末尾** |
| **Telemetry** | 仅用于人类观察系统运行情况的指标 | 前端 / DB / 日志 |

具体做法：

- `core/prompt_builder.py` 是**唯一**的 Prompt 组装入口。它的参数只能接受
  `str` 和 `AgentVisibleState`；任何 telemetry 对象（`TokenUsage` /
  `RuntimeTelemetry` / `ExecutionEvent`）传进来都会抛 `TelemetryLeakError`。
  这不是文档约定，是运行时守卫 —— 见 `test_prompt_builder.py`。
- `AgentVisibleState` 的数据模型里**根本没有** token / latency / cost / 时间戳 / id 字段，
  所以没有东西可以泄漏。
- Agent State 始终以固定横幅结尾：

  ```
  ===== INTERNAL AGENT STATE =====
  user_goal: ...
  current_stage: extracting
  available_agents: main, local_extractor, ...
  steps_remaining: 7
  completed_steps:
    - LocalExtractorAgent: ...
  important_results:
    [LocalExtractorAgent]: {"name": "张三", ...}
  ```

- 后端有测试断言「任何一条实际发出的 Prompt 里都不包含
  `prompt_tokens` / `latency_ms` / `execution_id` 等字样」。

## 13. Token 统计的真实性

`TokenUsage` 是 DeepSeek 与 MiniCPM **共用**的数据结构，每次模型调用记录：
`agent_name, model, prompt_tokens, completion_tokens, total_tokens, reasoning_tokens,
cached_tokens, request_start_time, request_end_time, latency_ms, tokens_per_second,
cost_usd, retries`。

诚实性规则：

- 优先使用 API 返回的 `usage.*`。
- API 没返回的字段一律为 **`null`**，界面显示 `—`，**绝不伪造**。
- `reasoning_tokens`：llama.cpp 不返回该值，因此本地推理的 reasoning token 是**本地估算**，
  并被打上 `reasoning_tokens_source = "estimated"`，界面上显示 `est` 小标签明确标注。
- **成本**：只有你在 `.env` 里显式配置了 `DEEPSEEK_PRICE_INPUT/OUTPUT` 才会计算，
  否则显示 `N/A`；本地模型显示 `Local` 而不是 `$0.000000`。
- 重试会把两次调用的 Token 累加，不会让重试「看起来更便宜」。

## 14. 容错与降级

| 情况 | 行为 |
| --- | --- |
| MiniCPM 离线 / 未启动 | 启动时探测到，前端提示「Local model offline」；MainAgent 自己完成，并在回答里简短说明 |
| 本地 Worker 被禁用（Settings） | 同上，走 DeepSeek |
| Worker 超时 / 报错 | 失败的 Agent 在 Timeline 中标记为 Failed，错误折叠进 Agent State，MainAgent 接管；**整个任务不会失败** |
| Worker 输出无法解析 | 保留原文交给 MainAgent（它仍然能读懂），不判失败 |
| MainAgent 决策 JSON 解析失败 | 逐级修复；最终降级为「把原文当回答」 |
| MainAgent 一直委派不肯收尾 | 达到 `MAX_AGENT_STEPS` 后进入 finalize 阶梯：先要求 JSON 回答 → 更强硬地要求 → 最后要求纯文本并用流式输出。任务以回答结束，而不是以失败结束 |
| DeepSeek 网络错误 | 有限次指数退避重试（默认 2 次），失败后给出可读错误信息（不是 500） |

Retry 永远是**有界**的（默认 `max_retry = 2`，`MAX_AGENT_STEPS = 12`）。

## 15. 目录结构

```text
MultAgentDev/
├── backend/
│   ├── main.py                     # FastAPI 入口 + lifespan 装配
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── api/                        # HTTP 层（薄）
│   │   ├── container.py            # 组合根：依赖在这里构造
│   │   ├── deps.py
│   │   ├── chat.py                 # POST /api/chat, GET /api/events/{id}
│   │   ├── executions.py           # 执行详情 / 步骤 / 遥测
│   │   ├── health.py               # 健康检查、模型状态、Agent 目录
│   │   ├── stats.py                # 会话统计
│   │   └── settings.py             # 运行时设置
│   ├── core/                       # 横切关注点
│   │   ├── config.py               # 唯一读取 .env 的地方
│   │   ├── prompt_builder.py       # ★ 唯一的 Prompt 组装入口（含遥测守卫）
│   │   ├── logging_setup.py        # 结构化日志（自动脱敏）
│   │   └── net.py                  # 代理环境变量净化
│   ├── agents/                     # Agent 实现
│   │   ├── base.py                 # BaseAgent / WorkerAgent
│   │   ├── main_agent.py           # ★ DeepSeek MainAgent
│   │   ├── local_agent.py          # ★ 本地 Worker 共享调用层
│   │   ├── extractor.py / summarizer.py / classifier.py / reviewer.py
│   ├── orchestration/              # ★ 运行时核心
│   │   ├── orchestrator.py         # 执行循环与调度
│   │   ├── router.py               # 路由规则与降级判定
│   │   ├── state_manager.py        # Agent-visible state 的唯一生产者
│   │   ├── context_manager.py      # 历史窗口、摘要、Worker 载荷裁剪
│   │   ├── decision_parser.py      # 容错 JSON 解析
│   │   ├── prompt_loader.py        # Prompt 文件加载 + 缓存
│   │   └── registry.py             # Agent Registry
│   ├── models/                     # Pydantic 数据契约
│   │   ├── schemas.py              # AgentVisibleState / RuntimeTelemetry / ...
│   │   ├── events.py               # SSE 事件
│   │   └── token_usage.py          # TokenUsage / 聚合
│   ├── providers/                  # 模型层抽象
│   │   ├── base.py                 # LLMProvider 接口
│   │   ├── deepseek.py
│   │   └── llama_cpp.py
│   ├── services/
│   │   ├── event_bus.py            # 可回放的事件总线（SSE 传输）
│   │   ├── token_tracker.py        # 唯一记录 Token 的地方
│   │   ├── runtime_state.py        # 用户可见实时状态
│   │   └── model_health.py         # 模型可用性探测（带缓存）
│   ├── prompts/                    # ★ Prompt 全部是独立 .md 文件
│   │   ├── _static_prefix.md       # 可缓存的静态前缀（CACHE_PREFIX_V1）
│   │   ├── main_agent.md
│   │   ├── extractor.md / summarizer.md / classifier.md / reviewer.md
│   ├── db/
│   │   ├── models.py               # SQLAlchemy 表定义
│   │   └── database.py             # 异步仓储
│   ├── tests/                      # 154 个测试
│   └── scripts/                    # 手工验证脚本
├── frontend/
│   ├── src/
│   │   ├── api/client.ts           # 类型化 API 客户端 + SSE
│   │   ├── store/useStore.ts       # ★ 事件流 → UI 状态归约
│   │   ├── components/
│   │   │   ├── agents/AgentPanel.tsx
│   │   │   ├── chat/               # ChatPanel / Markdown / CurrentAgentBanner
│   │   │   ├── execution/          # Timeline / Flow / Tokens / Session / StepDetail
│   │   │   ├── layout/             # Header / HistoryDrawer / SettingsModal
│   │   │   └── ui/                 # StatusDot / Disclosure 等原子组件
│   │   ├── lib/                    # agents.ts / format.ts
│   │   └── types/index.ts
│   ├── tailwind.config.js          # 明暗双主题色板（CSS 变量驱动）
│   └── vite.config.js
├── data/multiagent.db              # SQLite（自动创建，已被 git 忽略）
├── scripts/
│   ├── start_backend.ps1
│   ├── start_frontend.ps1
│   └── start_llama_server.ps1
├── start.ps1                       # 一键启动
├── .env.example
├── .gitignore
└── README.md
```

## 16. 后端 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat` | 发起一次请求，**立即**返回 `execution_id`（202） |
| `GET` | `/api/events/{execution_id}` | **SSE** 事件流（含历史回放） |
| `POST` | `/api/executions/{id}/cancel` | 取消执行 |
| `GET` | `/api/conversations` | 会话列表 |
| `GET` | `/api/conversations/{id}` | 会话详情 |
| `GET` | `/api/conversations/{id}/messages` | 消息列表 |
| `GET` | `/api/conversations/{id}/executions` | 该会话的执行记录 |
| `DELETE` | `/api/conversations/{id}` | 删除会话（级联） |
| `GET` | `/api/executions/{id}` | 执行详情（含步骤与 Token） |
| `GET` | `/api/executions/{id}/steps` | 时间线步骤 |
| `GET` | `/api/executions/{id}/calls` | 原始模型调用遥测 |
| `GET` | `/api/executions/{id}/state` | 实时遥测（Debug 下含内部状态） |
| `GET` | `/api/stats` | 会话累计统计 |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/models/status` | 两个模型的可用性 |
| `GET` | `/api/agents` | Agent 目录 |
| `GET/PATCH` | `/api/settings` | 读取 / 修改运行时设置 |
| `POST` | `/api/settings/reset` \| `/reload` \| `/validate` | 重置 / 重读 `.env` / 测试连通性 |

### SSE 事件

```json
{"type":"execution_started","execution_id":"...","status":"running"}
{"type":"agent_started","execution_id":"...","agent":"local_extractor","model":"MiniCPM5-2B","is_local":true,"stage":"extracting"}
{"type":"decision","execution_id":"...","agent":"main","data":{"action":"delegate","agent":"local_extractor","parse_ok":true}}
{"type":"agent_reasoning","execution_id":"...","agent":"local_extractor","text":"..."}
{"type":"token_usage","execution_id":"...","agent":"local_extractor","usage":{"prompt_tokens":120,"completion_tokens":83}}
{"type":"agent_completed","execution_id":"...","agent":"local_extractor"}
{"type":"final_answer","execution_id":"...","text":"..."}
{"type":"execution_completed","execution_id":"...","status":"completed"}
```

前端用 `fetch` + `ReadableStream` 消费 SSE（而不是 `EventSource`），因为需要干净的 abort
句柄；事件总线保留回放缓冲，所以即使浏览器在 `POST /api/chat` 之后几百毫秒才连上，
也不会漏掉任何一帧。

## 17. 数据库

SQLite，位置：**`data/multiagent.db`**（首次启动自动创建）。

表：`conversations` / `messages` / `executions` / `agent_steps` / `model_calls`。

关掉页面重新打开后：历史对话、历史执行、每一步的 Token 与 reasoning 都还在
（`get_execution` 会把 `model_calls` 折叠回对应步骤，保证重新打开的 Timeline 与实时
执行时看到的数字一致）。

## 18. 测试与验证

```powershell
conda activate MultiAgent
cd backend
python -m pytest tests/ -q
```

**154 个测试，全部通过**，覆盖：

| 文件 | 覆盖内容 |
| --- | --- |
| `test_decision_parser.py` | JSON 容错全阶梯（含截断补全、协议漂移、垃圾输入不崩溃） |
| `test_token_tracker.py` | Token 推导、聚合、诚实性（缺字段保持 null）、估算标注 |
| `test_prompt_builder.py` | ★ Prompt 顺序、Agent State 必须在末尾、**遥测对象被拒绝** |
| `test_fallback.py` | 路由与全部降级路径 |
| `test_orchestrator.py` | 端到端调度、委派、步骤预算、Worker 失败吸收、历史窗口、**遥测不进入 Prompt** |
| `test_database.py` | 持久化、级联删除、统计聚合、步骤用量还原 |
| `test_api.py` | HTTP 契约、SSE 回放、错误友好性 |
| `test_imports.py` | 模块可导入、路由齐全、默认只绑定 127.0.0.1、无硬编码 Key |
| `test_net.py` | 代理环境变量净化 |
| `test_settings_runtime.py` | 运行时设置真的生效（而不是改了个副本） |

### 手工验证脚本（会消耗真实 Token）

```powershell
cd backend
python scripts\verify_live.py      # 健康检查 + 本地模型 reasoning + 两个 Demo + 遥测隔离
python scripts\verify_proxy.py     # 走前端代理的完整链路（SSE 到最终回答）
python scripts\verify_fallback.py  # 本地 Worker 禁用 / 端点不可达 两种降级
```

## 19. 常见问题

**Q: 页面显示 “Local model offline”，能用吗？**
能，完全正常。系统会自动降级为 DeepSeek 单独完成，并在界面上提示。
启动 llama-server 后约 10 秒内会自动恢复（健康状态有短缓存）。

**Q: 一直提示连不上后端？**
确认后端在 8000 端口：`Invoke-RestMethod http://127.0.0.1:8000/api/health`。

**Q: 报错 `Invalid port: ':1]'` / 所有请求都失败？**
系统 `NO_PROXY` 里有 Windows 常见的 `[::1]` 写法，会让 HTTP 客户端在解析时就崩。
本项目在 `backend/core/net.py` **导入时**自动净化该变量，正常情况下无需处理。
如果你在别处遇到，手动设置即可：

```powershell
$env:NO_PROXY = "localhost,127.0.0.1,::1"
```

**Q: pip 装不上依赖？**
多半是网络。换镜像：

```powershell
python -m pip install -r backend\requirements.txt `
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

**Q: npm install 失败？**
项目自带 `frontend/.npmrc`，把 npm 缓存放在仓库内（避免全局缓存权限问题）。
国内镜像已指向 `registry.npmmirror.com`。

**Q: 本地模型总是返回空内容？**
MiniCPM5-2B 的思考预算被吃满时会不产出 `content`。系统已内置一次自动重试（加倍预算），
并且把 reasoning 标注为非最终答案返回。可以在 Settings 里把该 Agent 的 reasoning 调到
`low`/`medium`，或提高 `WORKER_MAX_TOKENS`。

**Q: 为什么有些 Token 数字显示 `—`？**
因为 API 没返回该字段。本项目不伪造数据；确实需要估算的值会标注 `est`。

**Q: 成本显示 `N/A`？**
没有配置价格。在 `.env` 里设置 `DEEPSEEK_PRICE_INPUT` / `DEEPSEEK_PRICE_OUTPUT`
（USD / 1M tokens）即可计算。本地模型永远显示 `Local`。

**Q: 怎么确认 Agent State 没被遥测污染？**
开启 **Debug Mode**，点开 Timeline 中任意一步：`Reasoning` 默认折叠并标注
「not the answer」；`/api/executions/{id}/state` 在 Debug 下会额外返回
`internal` 区块（明确标注 “INTERNAL — agent-visible state”），与用户可见遥测分开呈现。

## 20. 已知限制与后续扩展

### 当前限制

- **单机单实例**：内存中的运行时状态（事件总线、实时 Token 汇总）不跨进程共享，
  多 worker 部署需要引入 Redis 之类的共享层。
- **SQLite**：适合本地单用户；高并发写会受限于单写者模型。
- **无鉴权**：仅绑定 `127.0.0.1`，为本地使用设计；开放到局域网前必须加认证。
- **本地模型：并发已具备，但上下文被切分**。实测你的 llama-server 已经在跑
  **4 个并行 slot**（`total_slots: 4`），所以并非「串行」；但 `--ctx-size 16384` 是
  **所有 slot 共享**的，即每个 slot 实际只有约 4096 tokens，而 Worker 的
  `worker_max_tokens` 就是 2048，余量偏紧。推荐组合：
  `--ctx-size 65536 --parallel 4 -ctk q8_0 -ctv q8_0`（Q8 KV cache 才能塞进 8GB 显存）。
  另外 orchestrator 目前是**顺序**调度 worker 的，`AgentDecision.parallel` 字段已预留但未启用。
- **历史摘要是确定性的截断**，没有调用小模型做归纳（可以接上，但会增加延迟与不可控性）。
- **前端未做移动端深度优化**：基本可用，但主要面向 1920×1080 / 2560×1440 / 16:10 笔记本。
  在窄屏下左右两栏会隐藏，导致看不到 Timeline 与 Token。

### 后续可以扩展

完整方案、代码片段与「什么时候才值得做」的判断标准见
**[docs/LIMITATIONS.md](docs/LIMITATIONS.md)**。摘要：

- **更多 Worker**：`CodingAgent`、`SearchAgent`、`RAGAgent`、`VisionAgent` —— 实现 `WorkerAgent`
  子类并在 `orchestration/registry.py` 注册即可，其余部分无需改动。
- **更多 Provider**：`LLMProvider` 接口已经统一（`generate` / `stream` / `health_check`），
  新增 OpenAI / Gemini / Qwen / Ollama 只需加一个文件。
- **并行委派**：`AgentDecision.parallel` 字段已经预留，结合本地 4 个 slot 可在 orchestrator
  中做 `asyncio.gather`（注意前端 Timeline 需改为按 `step_index` 排序）。
- **历史摘要模型化**：异步增量摘要 + 按 `(conversation_id, last_message_id)` 缓存 +
  失败回退到现有确定性截断。
- **多进程 / 多机**：需要时把事件总线、实时 Token 汇总、运行时状态迁到 Redis；
  单机自用**不建议**提前引入。
- **鉴权**：一旦要开放局域网，只需加一个 `API_ACCESS_TOKEN` 依赖 + 前端带 header，
  见 docs/LIMITATIONS.md 第 3 节的完整实现。
- **成本分析**：配置价格后已有 `cost_usd` 字段，可做按天/按 Agent 的成本报表。

---

## 许可

仅供本地学习与研究使用。DeepSeek 与 MiniCPM 的模型许可以各自官方声明为准。
