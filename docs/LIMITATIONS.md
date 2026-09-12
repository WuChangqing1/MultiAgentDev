# 已知限制与优化路线（Limitations & Optimisation Roadmap）

本文档对 6 个已知限制逐条给出：**现状 → 影响面 → 具体优化方案 → 何时才值得做**。

建议顺序（投入产出比从高到低）：

| 优先级 | 项目 | 工作量 | 何时做 |
| --- | --- | --- | --- |
| ★★★ | 本地模型并发（已存在 4 个 slot，只需调参 + 用起来） | 0.5 天 | 立刻可做 |
| ★★★ | 历史摘要改为模型归纳 | 1 天 | 立刻可做 |
| ★★ | 移动端可用性 | 1 天 | 有手机查看需求时 |
| ★★ | SQLite → PostgreSQL | 1 天 | 多用户 / 高并发时 |
| ★★ | 无鉴权 | 0.5 天 | **一旦要开放局域网就必须做** |
| ★ | 单机单实例（内存态） | 3–5 天 | 需要水平扩展时（当前很可能不需要） |

---

## 1. 单机单实例：内存态不跨进程

### 现状

以下状态**只存在于进程内存**：

| 状态 | 位置 | 丢失后果 |
| --- | --- | --- |
| 事件总线 backlog + 订阅者 | `services/event_bus.py` | 重启后旧执行无法再订阅 SSE |
| 实时 Token 汇总 | `services/token_tracker.py` | 「当前请求 Token」面板清空 |
| 实时遥测 / Agent 状态 | `services/runtime_state.py` | Current Agent 横幅无数据 |
| 运行中的 asyncio 任务句柄 | `orchestration/orchestrator.py` `self._tasks` | 重启后在跑的执行丢失 |
| 健康探测缓存 | `services/model_health.py` | 仅影响首次响应速度 |

**好消息**：真正的**事实来源已经在 SQLite**（conversations / messages / executions /
agent_steps / model_calls）。重启后历史对话、历史执行、Token 统计都还在
—— 已实测验证。丢的只是「正在进行中的实时流」。

**影响面**：单用户本地使用下几乎无影响。只有当你用
`uvicorn --workers 4` 或多机部署时才会出问题：请求可能落到没有对应事件总线的进程上，
SSE 永远等不到事件。

### 优化方案

**方案 A（推荐，成本最低）：保持单进程，但显式禁止多 worker**

在 `backend/main.py` 启动时探测并警告，避免有人误用 `--workers`：

```python
# 放在 lifespan 里
import os
if int(os.environ.get("WEB_CONCURRENCY", "1")) > 1:
    log.warning(
        "in_memory_state_not_shared",
        extra={"workers": os.environ["WEB_CONCURRENCY"],
               "detail": "SSE and live telemetry are per-process; run a single worker."},
    )
```

配合 README 写清楚：**不要**用 `--workers N`。

**方案 B：Redis 外置共享层（需要多进程时）**

引入 `redis>=5.0` + `redis.asyncio`，替换三个组件：

```python
# services/event_bus.py
class RedisEventBus:
    """事件总线改为 Redis Pub/Sub + List 回放缓冲。

    - publish:  LPUSH exec:{id}:events  +  PUBLISH exec:{id}
    - backlog:  LRANGE exec:{id}:events 0 -1（配 LTRIM 保留最近 2000 条）
    - subscribe: SUBSCRIBE exec:{id}，先回放 list 再切 pubsub
    """
```

```python
# services/token_tracker.py
# 实时汇总改为 Redis Hash：HINCRBY exec:{id} prompt_tokens 120 ...
# 聚合读取用 HGETALL，天然跨进程一致
```

```python
# services/runtime_state.py
# RuntimeTelemetry 存 Redis String（JSON，带 TTL 1h）
# Agent 状态存 Redis Hash
```

**方案 C：任务队列（真正需要水平扩展时）**

把 `orchestrator.start()` 里的 `asyncio.create_task` 换成
Celery / Arq / Dramatiq 投递，worker 进程执行，SSE 通过 Redis Pub/Sub 回传。
这是最大改动，只有当单机算力真的不够时才做。

### 判断标准

> **除非你要么多机部署、要么 CPU 推理慢到需要并行跑多个请求，否则不要做方案 B/C。**
> 现在的架构在单机上是对的，过早引入 Redis 只会增加一个必须运维的组件。

---

## 2. SQLite 单写者

### 现状

已启用 WAL（`db/database.py` 的 connect 钩子里 `PRAGMA journal_mode=WAL`）
+ `busy_timeout=5000`。这意味着**并发读不受写阻塞**，但写仍然是串行的。

单次执行会写入：1 行 execution + N 行 agent_steps + M 行 model_calls
（实测一次 3 步执行 ≈ 8 次写）。本地单用户下完全够用。

**瓶颈点**：`db/database.py` 里每个方法各自 `async with self.session()`，
一次执行会开很多短事务。并发请求多时写锁竞争会显现。

### 优化方案

**阶段 1：写合并（低成本，收益明显）**

把 `model_calls` 的逐条写入改成批量提交。改动点在
`db/database.py::add_model_call` —— 增加一个缓冲队列，
在 `_finalize()` 时一次性 `session.add_all()`：

```python
# services/token_tracker.py 增加
self._pending: dict[str, list[ModelCallRow]] = {}

def queue(self, execution_id: str, usage: TokenUsage) -> None:
    """先入内存队列，避免每次模型调用都开一个写事务。"""
    self._pending.setdefault(execution_id, []).append(_to_row(usage))

async def flush(self, execution_id: str) -> None:
    rows = self._pending.pop(execution_id, [])
    if rows:
        await self._db.add_model_calls_bulk(rows)
```

在 `orchestrator._finalize()` 里调用 `flush()`。
预期：写事务数从 8 降到 3 左右。

**阶段 2：迁移 PostgreSQL（多用户时）**

改动其实很小，因为已经全面使用异步 SQLAlchemy：

```powershell
python -m pip install asyncpg alembic
```

```env
# .env
DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/multiagent
```

```python
# db/database.py 的 connect() 里已经按 url 分支处理 sqlite pragma，
# 只需确认非 sqlite 时不执行 PRAGMA（当前代码已用 url.startswith("sqlite") 判断 ✓）
```

顺手加 Alembic 管理迁移（目前是 `Base.metadata.create_all`，改表结构会丢数据）：

```powershell
alembic init backend/db/migrations
alembic revision --autogenerate -m "initial"
```

**阶段 3：历史归档**

`data/` 会无限增长（含完整 Prompt 文本）。加一个清理任务：

```python
# 保留最近 90 天，或按大小裁剪
DELETE FROM agent_steps WHERE execution_id IN (
    SELECT id FROM executions WHERE created_at < date('now', '-90 days')
);
```

注意：`agent_steps.input` 存了完整 Prompt，是体积大头。

### 判断标准

> 单用户本地：**什么都不用做**。
> 并发请求 > 5 或库 > 500MB：做阶段 1 + 阶段 3。
> 多用户 / 需要网络访问：做阶段 2。

---

## 3. 无鉴权（仅绑定 127.0.0.1）

### 现状

- 只绑定 `127.0.0.1`（`core/config.py` 的 `host` 默认值，且
  `test_imports.py::test_app_binds_to_loopback_by_default` 会断言这一点）
- CORS 只允许 `127.0.0.1:5173` / `localhost:5173`
- **没有任何认证**：任何能访问该端口的人都能聊天、改设置、读全部历史
- DeepSeek API Key 只在服务端，前端拿不到明文（Settings 只返回指纹）

**风险**：当前配置下风险可接受（只有本机能连）。但只要改成 `0.0.0.0`
或做端口转发，就立刻变成「任何人都能用你的 DeepSeek 额度读你的历史」。

### 优化方案

**阶段 1：开放前的强制门槛（必做）**

```python
# backend/api/deps.py 增加
import secrets
from fastapi import Header, HTTPException, status

async def require_token(x_api_token: str = Header(default="")) -> None:
    expected = effective_settings().api_access_token
    if not expected:
        return  # 未配置 = 本地模式，不做校验
    if not secrets.compare_digest(x_api_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or missing API token.")
```

```env
# .env（新增）
API_ACCESS_TOKEN=            # 留空 = 本地免鉴权模式
```

然后在 `main.py` 里对需要保护的路由挂依赖：

```python
app.include_router(chat.router, dependencies=[Depends(require_token)])
app.include_router(conversations.router, dependencies=[Depends(require_token)])
app.include_router(settings_api.router, dependencies=[Depends(require_token)])
# /api/health 保持公开，便于探活
```

前端在 `api/client.ts` 的 `request()` 里带上 header：

```typescript
headers: { 'Content-Type': 'application/json', 'X-Api-Token': getToken() }
```

**阶段 2：多用户（如果真需要）**

- 用户表 + 密码哈希（`passlib[bcrypt]` 或 `argon2-cffi`）
- 会话表加 `user_id` 外键，所有查询按 `user_id` 过滤
- 用 JWT（`python-jose`）或服务端 session
- 注意：**必须同时给 `conversations` / `executions` 查询加 owner 校验**，
  否则就是水平越权漏洞

**阶段 3：其他加固**

```python
# main.py 加安全响应头
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
```

限流（防误刷 DeepSeek 额度）：

```powershell
python -m pip install slowapi
```

```python
from slowapi import Limiter
limiter = Limiter(key_func=lambda r: r.client.host)

@router.post("/chat")
@limiter.limit("20/minute")
async def create_chat(...): ...
```

顺便：`Settings` 面板的写操作建议单独限流，因为它能改 endpoint。

### 判断标准

> **保持不变更 `HOST`。** 如果确实要局域网访问：
> 阶段 1 是**必做项**，不是可选项。

---

## 4. 本地模型串行

### 现状 —— 这一条比预想的要好

实测你的 llama-server 属性：

```json
{ "total_slots": 4, "default_generation_settings": { "n_ctx": 16384 } }
```

**llama-server 已经在跑 4 个并行 slot**（`--parallel` 默认 auto）。
所以「串行」这个描述**不准确**：服务端本来就能同时处理 4 个请求。

**但有一个真实隐患**：`n_ctx: 16384` 是**所有 slot 共享的总上下文**，
即每个 slot 实际只有 `16384 / 4 = 4096` tokens。而 Worker 的
`worker_max_tokens` 是 2048，加上 Prompt 很容易接近甚至超过 4096。

### 优化方案

**方案 A（推荐）：给足上下文，保留并发**

```powershell
.\scripts\start_llama_server.ps1 -ContextSize 65536
```

即 `--ctx-size 65536 --parallel 4` → 每 slot 16384，与模型原生窗口一致。
KV cache 显存估算（MiniCPM5-2B：36 层，n_embd 2048，GQA 8 KV heads → head_dim 128）：

```
每 token 每层 KV = 2 * 8 * 128 * 2 bytes(fp16) = 4096 bytes ≈ 4 KB
65536 tokens * 36 层 * 4 KB ≈ 9.4 GB
```

8GB 显存放不下。所以实际二选一：

**方案 B：降到 2 个 slot（平衡）**

```powershell
.\scripts\start_llama_server.ps1 -ContextSize 32768
# 手动加 --parallel 2 → 每 slot 16384
# KV cache ≈ 32768 * 36 * 4KB ≈ 4.7 GB  ← 8GB 卡可行
```

**方案 C：KV cache 量化（省一半）**

```powershell
& llama-server.exe ... -ctk q8_0 -ctv q8_0 --ctx-size 65536 --parallel 4
# KV 从 fp16 降到 q8_0 → 约 4.7 GB，质量损失极小
```

这是 8GB 卡上最划算的组合：**64K 总上下文 + 4 slot + Q8 KV cache**。

**方案 D：真正用起来并发**

目前 orchestrator 是**顺序**执行 worker 的。`AgentDecision.parallel`
字段已经预留但未使用。要真正并行，改
`orchestration/orchestrator.py` 的 worker 分支：

```python
if decision.parallel and len(decision.tasks) > 1:
    results = await asyncio.gather(*[
        self._run_worker(..., decision_task=t) for t in decision.tasks
    ])
```

同时 `AgentDecision` 需要支持 `tasks: list[...]`。
**注意**：并行会让 Token 顺序不再确定，Timeline 需要按 `step_index` 而非到达顺序排序
（当前前端 `reduceEvent` 是按到达顺序 append 的，需要改为按索引插入）。

**方案 E：多实例（真的需要 > 4 并发时）**

跑两个 llama-server（一个 8080、一个 8081），
`LOCAL_MODEL_BASE_URL` 改成列表，provider 层做轮询或最少连接调度。
Orchestrator 侧需要一个本地信号量限制并发数不超过 slot 数。

### 判断标准

> **立刻可做**：方案 C（Q8 KV cache + 64K 上下文）。
> **有并行的实际需求时**：方案 D。
> **不建议**在没有真实并发需求时做方案 E。

---

## 5. 历史摘要为确定性截断，而非模型归纳

### 现状

`orchestration/context_manager.py::digest()` 的实现是：保留头部 1/3 + 尾部 2/3，
中间用 `...[middle omitted]...` 截断。**不调用任何模型。**

原因（当初的设计取舍）：摘要若调用模型，会给每次请求增加一次不可控的延迟，
而且可能引入幻觉 —— 一个被「归纳错误」的历史比一个被截断的历史更危险。

**影响**：长对话里，中间部分的信息会真的丢掉（对 MainAgent 不可见）。

### 优化方案：分层摘要（推荐）

关键思路：**把摘要从请求路径上挪走**，异步做，并且保留原文兜底。

**第 1 步：异步增量摘要**

```python
# orchestration/context_manager.py 扩展
class ContextManager:
    def __init__(self, ..., summarizer: "SummarizerAgent | None" = None):
        self._summarizer = summarizer

    async def digest_async(self, older: list[Message]) -> str:
        """用本地 MiniCPM 归纳旧对话。

        失败时**无条件**回退到确定性截断 —— 摘要是优化，不是依赖。
        """
        if self._summarizer is None or not older:
            return self.digest(older)

        transcript = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}" for m in older
        )
        try:
            result = await self._summarizer.run(
                AgentTask(
                    task_id="digest",
                    agent="local_summarizer",
                    instruction=(
                        "Summarise the conversation below. Preserve every concrete fact: "
                        "names, numbers, decisions, constraints, unfinished requests. "
                        "Drop pleasantries. At most 300 words."
                    ),
                    context=transcript[:12000],
                ),
                AgentVisibleState(user_goal="compress conversation history"),
            )
            return result.output.strip() or self.digest(older)
        except Exception:
            log.warning("digest_summarisation_failed", exc_info=True)
            return self.digest(older)   # ← 永远有兜底
```

**第 2 步：缓存，避免重复摘要**

摘要是纯函数（输入不变 → 输出不变），所以可以缓存：

```python
# 用 (conversation_id, last_message_id) 作为缓存键
# 存到 messages 表旁边新增一列，或单独的 conversation_digests 表
CREATE TABLE conversation_digests (
    conversation_id TEXT PRIMARY KEY,
    up_to_message_id INTEGER NOT NULL,
    digest TEXT NOT NULL,
    token_estimate INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

只有 `up_to_message_id` 变化时才重新摘要。这样**一个 50 轮的对话只会摘要几次**，
而不是每轮都摘要。

**第 3 步：后台预热**

在对话空闲时（例如上一轮结束后 30 秒）后台触发摘要，
这样下一轮请求里 `digest_async` 基本总是命中缓存，**不增加任何请求延迟**。

**第 4 步（可选）：两级摘要**

```
[很久以前] → 已归档摘要（每 20 轮压缩一次）
[较久]     → 滚动摘要
[最近 6 条] → 原文
```

### 关键安全约束

**摘要绝不能替代原文进入「必须精确」的场景。** 具体来说：
Worker 拿到的 `context` 必须始终是 MainAgent 显式提供的原文，
不能是摘要 —— 这一点当前架构已经保证了（`_fallback_context()` 只取最近的
`important_results`，不碰历史），改摘要时要保持。

### 判断标准

> 对话经常超过 20 轮 → 值得做。否则确定性截断完全够用，且更可预测。

---

## 6. 移动端仅基本可用

### 现状

布局是 `lg:flex` / `xl:block` 的桌面优先设计：

```tsx
// App.tsx
<div className="hidden w-56 shrink-0 ... lg:flex">      {/* 左栏：<1024px 隐藏 */}
<div className="min-w-0 flex-1">                        {/* 中栏：始终显示 */}
<div className="hidden w-[19rem] ... xl:block">         {/* 右栏：<1280px 隐藏 */}
```

**问题**：手机上（<1024px）隐藏了左栏和右栏，于是
**「谁在工作 / Token / Timeline」全部看不到** —— 而这恰恰是本项目的核心价值。

### 优化方案

**第 1 步：把右栏改成移动端底部抽屉（最高优先级）**

```tsx
// App.tsx
const [mobilePanel, setMobilePanel] = useState(false)

// 中栏下方加一个移动端触发条
<button
  onClick={() => setMobilePanel(true)}
  className="flex items-center gap-2 border-t border-hairline px-3 py-2 lg:hidden"
>
  <StatusDot status={currentStatus} showRing />
  <span className="text-xs">{currentAgentLabel}</span>
  <span className="ml-auto font-mono text-2xs">{totalTokens} tok</span>
  <ChevronUp className="h-3.5 w-3.5" />
</button>

// 抽屉复用现有 ExecutionPanel，零重复代码
{mobilePanel && (
  <div className="fixed inset-x-0 bottom-0 z-40 h-[70vh] lg:hidden">
    <ExecutionPanel />
  </div>
)}
```

这样移动端**功能完全对齐**，只是呈现方式不同。

**第 2 步：左栏改成 Sheet**

同样的模式：`AgentPanel` 放进一个从左侧滑出的抽屉，
Header 上加一个汉堡按钮（`lg:hidden`）。

**第 3 步：修掉 Input 的 iOS 问题**

```css
/* index.css */
textarea {
  font-size: 16px;  /* 小于 16px 时 iOS Safari 会自动放大页面 */
}
@media (min-width: 640px) {
  textarea { font-size: 0.9375rem; }
}
```

**第 4 步：安全区适配（刘海屏）**

```tsx
<footer className="pb-[env(safe-area-inset-bottom)]">
```

**第 5 步：触屏体验**

- 时间线行高从 `py-1.5` 提到 `py-2.5`（≥44px 触摸目标）
- 复制按钮在触屏上改为**始终可见**（现在是 `opacity-0 group-hover:opacity-100`，
  触屏没有 hover）
- 代码块允许横向滚动（已经是 `overflow-x-auto` ✓）

**第 6 步：性能**

移动端 CPU 弱，`react-syntax-highlighter` 是主要负担。
长回答里只高亮**可见区域**的代码块（`IntersectionObserver` 懒渲染），
或对超长代码块直接降级为 `<pre>`。

### 判断标准

> 需要在手机上看执行过程 → 做第 1、2、3 步（约 1 天）。
> 只是偶尔瞄一眼回答 → 现状够用。

---

## 附：不建议做的事

以下优化**看起来合理但当前不值得**，列出来是为了避免走弯路：

| 想法 | 为什么不建议 |
| --- | --- |
| 立刻上 Redis + Celery | 单机单用户下是纯粹的复杂度增量。先量测，再扩展 |
| 用 Docker Compose 全量容器化 | 你要连的是**宿主机**的 llama-server（`127.0.0.1:8080`），容器网络会让这件事变麻烦 |
| 给 Worker 加更多 Agent | 4 个已覆盖机械性任务的主要类别。加 Agent 前先确认 MainAgent 真的会路由过去 |
| 前端上 Next.js SSR | 这是一个本地工具，SSR 没有任何收益，只会增加构建复杂度 |
| 把 reasoning 直接展示给用户 | 明确违反需求。reasoning 只应存在于可折叠的 Debug 面板 |
| 把 Token 数字喂回 Prompt 帮助模型「自我节流」 | 明确违反需求 #23/#59，且实测中强模型并不需要这个信息 |
