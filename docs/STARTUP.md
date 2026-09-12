# 启动、重启与配置速查（Startup Guide）

> 本文件是 `README.md` 的补充，专门回答三个日常问题：
> **API Key 写在哪** · **重启后怎么启动** · **怎么确认一切正常**。

---

## 一、API Key 写在哪

### 唯一位置：项目根目录的 `.env`

```
D:\CodingData\Github\dsh\MultAgentDev\.env
```

打开它，找到这一行并填入你的 Key：

```env
DEEPSEEK_API_KEY=sk-你的真实key
```

完整示例：

```env
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

### 规则

| 事项 | 说明 |
| --- | --- |
| 文件位置 | **项目根目录**（不是 `backend/` 里） |
| 变量名 | `DEEPSEEK_API_KEY`（大小写不敏感） |
| 是否要引号 | **不要**加引号，直接写 `sk-...` |
| 是否要空格 | `=` 两边不要留空格 |
| `.env` 不存在 | 执行 `Copy-Item .env.example .env` |
| 改了之后 | 重启后端即可；或点 Settings → **Reset overrides** / 调 `POST /api/settings/reload` 重读 |
| 会不会被提交到 Git | **不会**，`.gitignore` 第 4 行就是 `.env`（已用 `git check-ignore` 验证） |
| 前端能不能看到 | **不能**。Settings 面板只显示指纹（`sk-5***…***e63`） |

### 检查 Key 是否被正确读到

```powershell
.\scripts\status.ps1
```

会输出：

```
-- .env configuration -------------------------------------------------
  [ OK ]  .env exists
  [ OK ]  DEEPSEEK_API_KEY set (sk-556..., 35 chars)
```

或者直接问后端：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | Select-Object deepseek
# configured = Key 已读到；unknown = 没读到
```

### 代码里有没有硬编码？

没有，而且有测试守着：`backend/tests/test_imports.py` 里的
`test_no_api_key_is_hardcoded_in_source` 会扫描全部源码，
一旦发现 `sk-` 开头的长字符串就让测试失败。同一个文件里还有
`test_env_file_is_git_ignored`，断言 `.env` 确实被 Git 忽略。

### 其他配置项

全部可配置项及注释在 **`.env.example`**。最常改的几个：

```env
# 换个 DeepSeek 模型
DEEPSEEK_MODEL=deepseek-chat          # 或 deepseek-reasoner

# 成本估算（不填就显示 N/A，不会瞎猜）
DEEPSEEK_PRICE_INPUT=0.27
DEEPSEEK_PRICE_OUTPUT=1.10

# 单次请求最多几步（防止无限委派）
MAX_AGENT_STEPS=12

# Worker 输出长度上限
WORKER_MAX_TOKENS=2048

# 关掉本地 Worker，全部走 DeepSeek
ENABLE_LOCAL_WORKERS=true
```

> 这些也可以在**运行中**从界面右上角 **Settings** 改（立即生效，只影响当前进程）。
> 只有 API Key 必须改 `.env`。

---

## 二、系统重启后怎么启动

**系统重启后，所有进程都没了**，需要重新启动三样东西：
本地模型 → 后端 → 前端。

### 最快方式：一条命令

```powershell
cd D:\CodingData\Github\dsh\MultAgentDev
.\start.ps1
```

`start.ps1` 会自动完成：定位 conda 环境 → 检查后端依赖 → 检查 `.env` 和 API Key →
探测本地模型 → 分别在两个新窗口启动后端和前端。

> ⚠️ `start.ps1` **不会**启动 llama-server（按需求 #54，脚本不碰用户自己的模型进程），
> 它只做探测。如果探测到没在跑，会在最后一行提示你。

### 完整方式：三个终端

```powershell
# ---------- 终端 1：本地模型 ----------
cd D:\CodingData\Github\dsh\MultAgentDev
.\scripts\start_llama_server.ps1
# 如果 8080 已经在跑，脚本检测到就会直接退出，不会打扰已有进程

# ---------- 终端 2：后端 ----------
conda activate MultiAgent
cd D:\CodingData\Github\dsh\MultAgentDev
.\scripts\start_backend.ps1

# ---------- 终端 3：前端 ----------
cd D:\CodingData\Github\dsh\MultAgentDev
.\scripts\start_frontend.ps1
```

然后浏览器打开 **http://127.0.0.1:5173**。

### 手动命令（等价，不用脚本）

```powershell
# 终端 1 —— 本地模型
& "D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp\llama-server.exe" `
  -m "D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp\models\MiniCPM5-2B\MiniCPM5-2B-Q8_0.gguf" `
  --alias MiniCPM5-2B --host 127.0.0.1 --port 8080 `
  --ctx-size 32768 --parallel 2 --n-gpu-layers 99 --jinja

# 终端 2 —— 后端
conda activate MultiAgent
cd D:\CodingData\Github\dsh\MultAgentDev\backend
uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# 终端 3 —— 前端
cd D:\CodingData\Github\dsh\MultAgentDev\frontend
npm run dev
```

### 启动顺序有要求吗

**没有严格要求**，三个组件互相独立启动都能活着：

- 后端可以在本地模型没起来时启动 → 自动降级为 DeepSeek-only，界面会提示
- 前端可以在后端没起来时启动 → 页面能开，API 调用报错，Header 会显示重试按钮
- 本地模型随时可以后补启动 → 约 10 秒内（健康缓存 TTL）自动恢复为可委派状态

推荐顺序只是为了让日志更干净：**模型 → 后端 → 前端**。

### 重启后会丢失什么

| 数据 | 重启后 | 说明 |
| --- | --- | --- |
| 历史对话 | ✅ 还在 | 存在 `data/multiagent.db` |
| 历史执行 / Timeline | ✅ 还在 | 同上 |
| Token 统计 | ✅ 还在 | 同上 |
| Settings 里的运行时改动 | ❌ 丢失 | 只在内存，重启回到 `.env` 的值 |
| 正在进行中的执行 | ❌ 丢失 | 内存态 asyncio 任务 |
| 「当前请求」实时面板 | ✅ 清空为初始态 | 这是预期的 |

---

## 三、怎么确认一切正常

### 一条命令看全部

```powershell
.\scripts\status.ps1
```

输出示例（正常状态）：

```
MultiAgent — component status
====================================================================
-- Conda environment 'MultiAgent' -----------------------------------
  [ OK ]  python found: D:\...\envs\MultiAgent\python.exe
  [ OK ]  backend dependencies installed
-- .env configuration -----------------------------------------------
  [ OK ]  .env exists
  [ OK ]  DEEPSEEK_API_KEY set (sk-556..., 35 chars)
-- llama-server (local MiniCPM worker) ------------------------------
  [ OK ]  online at http://127.0.0.1:8080 — serving: MiniCPM5-2B
          slots=4  total_ctx=16384  ->  ~4096 tokens per slot
-- Backend API (FastAPI) --------------------------------------------
  [ OK ]  online at http://127.0.0.1:8000
          deepseek=configured  minicpm=online  database=online  max_steps=12
-- Frontend dev server (Vite) ---------------------------------------
  [ OK ]  online at http://127.0.0.1:5173  (HTTP 200)
  [ OK ]  dev proxy -> backend works (5 agents)
-- Database ----------------------------------------------------------
  [ OK ]  data\multiagent.db  (152 KB)
```

`[WARN]` 通常可以接受（例如本地模型没开）。`[FAIL]` 才需要处理。

### 分开验证

```powershell
# 后端活着吗
Invoke-RestMethod http://127.0.0.1:8000/api/health

# 本地模型活着吗
Invoke-RestMethod http://127.0.0.1:8080/v1/models

# 前端活着吗，代理通不通
Invoke-RestMethod http://127.0.0.1:5173/api/agents
```

### 跑自动化验证（会消耗真实 Token）

```powershell
conda activate MultiAgent
cd backend

python scripts\verify_live.py      # 健康检查 + 本地 reasoning + 两个 Demo + 遥测隔离
python scripts\verify_proxy.py     # 走前端代理的完整链路，含 SSE
python scripts\verify_fallback.py  # 本地 Worker 禁用 / 端点不可达 两种降级

python -m pytest tests\ -q         # 154 个单元测试（不消耗 Token）
```

---

## 四、常见启动问题

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `.\start.ps1` 报「无法加载文件，未数字签名」 | PowerShell 执行策略 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| 提示环境找不到 | conda 环境名或路径不同 | `.\scripts\start_backend.ps1 -CondaEnvName 你的环境名` |
| 提示依赖缺失 | 没装或装到别的环境 | `conda activate MultiAgent` 然后 `python -m pip install -r backend\requirements.txt` |
| 页面能开但一直报连不上后端 | 后端没起，或端口冲突 | `.\scripts\status.ps1` 看哪一项 FAIL |
| 端口 8000 / 5173 被占用 | 上次的进程没退干净 | `Get-NetTCPConnection -LocalPort 8000` 找到 PID 后 `Stop-Process -Id <PID>` |
| 改了 `.env` 没生效 | 后端启动时读一次 | 重启后端，或 `Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/settings/reload` |
| Settings 改了但「好像没生效」 | 运行时改动只在内存 | 确认没有重启后端；或直接改 `.env` 更稳妥 |
| `pip install` 超时 | PyPI 网络 | 加 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple` |
| `npm install` 失败 | 缓存权限 | 项目自带 `frontend/.npmrc` 已把缓存放仓库内；仍失败可删 `frontend\.npm-cache` 重试 |
| 所有 HTTP 请求报 `Invalid port: ':1]'` | `NO_PROXY` 含 `[::1]` | 已在 `core/net.py` 自动兜住；若在别处遇到：`$env:NO_PROXY="localhost,127.0.0.1,::1"` |

---

## 五、关掉服务

各服务都在自己的窗口里运行，**在对应窗口按 `Ctrl+C`** 即可。

或者一次性按端口结束：

```powershell
foreach ($port in 8000, 5173) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force }
}
```

> 本项目的脚本**不会**去关闭 `llama-server`（那是你自己的模型进程）。
> 需要停它就切换到它的窗口按 `Ctrl+C`。
