# ============================================================================
#  Health check for every component.
#
#  Safe to run at any time -- it only reads state, it never starts or stops
#  anything. Use it after a reboot to see exactly what is missing.
#
#  Usage (from the project root):
#      .\scripts\status.ps1
# ============================================================================

[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [int]$LlamaPort = 8080,
    [string]$CondaEnvName = 'MultiAgent'
)

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot

# Windows PowerShell 5.1 defaults to the legacy console encoding, which turns
# non-ASCII output into mojibake. Force UTF-8 when the host supports it.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Write-Ok($m)   { Write-Host "  [ OK ]  $m" -ForegroundColor Green }
function Write-Bad($m)  { Write-Host "  [FAIL]  $m" -ForegroundColor Red }
function Write-Warn2($m){ Write-Host "  [WARN]  $m" -ForegroundColor Yellow }
function Write-Info($m) { Write-Host "          $m" -ForegroundColor DarkGray }
function Write-Head($m) {
    Write-Host ""
    Write-Host "-- $m " -ForegroundColor White -NoNewline
    Write-Host ("-" * [Math]::Max(0, 62 - $m.Length)) -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "MultiAgent - component status" -ForegroundColor Cyan
Write-Host ("=" * 68) -ForegroundColor DarkGray

# --- 1. conda environment ---------------------------------------------------
Write-Head "Conda environment '$CondaEnvName'"
$python = $null
if ($env:CONDA_DEFAULT_ENV -eq $CondaEnvName -and $env:CONDA_PREFIX) {
    $c = Join-Path $env:CONDA_PREFIX 'python.exe'
    if (Test-Path $c) { $python = $c }
}
if (-not $python) {
    foreach ($root in @(
        "$env:USERPROFILE\miniconda3", "$env:USERPROFILE\anaconda3",
        'C:\ProgramData\miniconda3', 'C:\ProgramData\Anaconda3',
        'D:\App\Business\Coding\Python\Miniconda'
    )) {
        $c = Join-Path $root "envs\$CondaEnvName\python.exe"
        if (Test-Path $c) { $python = $c; break }
    }
}
if ($python) {
    Write-Ok "python found: $python"
    $deps = & $python -c "import fastapi, uvicorn, openai, sqlalchemy, aiosqlite; print('ok')" 2>&1
    if ($deps -match 'ok') {
        Write-Ok "backend dependencies installed"
    } else {
        Write-Bad "backend dependencies missing"
        Write-Info "python -m pip install -r backend\requirements.txt"
    }
} else {
    Write-Bad "environment not found"
    Write-Info "conda create -n $CondaEnvName python=3.12"
}

# --- 2. .env ----------------------------------------------------------------
Write-Head ".env configuration"
$envFile = Join-Path $projectRoot '.env'
if (Test-Path $envFile) {
    Write-Ok ".env exists"
    if (Select-String -Path $envFile -Pattern '^\s*DEEPSEEK_API_KEY\s*=\s*\S+' -Quiet) {
        # Show a fingerprint only -- never the key itself.
        $key = (Select-String -Path $envFile -Pattern '^\s*DEEPSEEK_API_KEY\s*=\s*(\S+)').Matches[0].Groups[1].Value
        $fingerprint = if ($key.Length -gt 8) { $key.Substring(0, 4) + ('*' * 8) + $key.Substring($key.Length - 4) } else { '*' * $key.Length }
        Write-Ok "DEEPSEEK_API_KEY set: $fingerprint  ($($key.Length) chars)"
    } else {
        Write-Bad "DEEPSEEK_API_KEY is empty"
        Write-Info "edit $envFile and set DEEPSEEK_API_KEY=sk-..."
    }
} else {
    Write-Bad ".env missing"
    Write-Info "Copy-Item .env.example .env   then set DEEPSEEK_API_KEY"
}

# --- 3. llama-server --------------------------------------------------------
Write-Head "llama-server (local MiniCPM worker)"
try {
    $props = Invoke-RestMethod -Uri "http://127.0.0.1:$LlamaPort/v1/models" -TimeoutSec 5
    $ids = ($props.data | ForEach-Object { $_.id }) -join ', '
    Write-Ok "online at http://127.0.0.1:$LlamaPort - serving: $ids"

    try {
        $p = Invoke-RestMethod -Uri "http://127.0.0.1:$LlamaPort/props" -TimeoutSec 5
        $slots = $p.total_slots
        $ctx = $p.default_generation_settings.n_ctx
        Write-Info "slots=$slots  total_ctx=$ctx  ->  ~$([Math]::Floor($ctx / [Math]::Max($slots,1))) tokens per slot"
        if ($slots -gt 1 -and ($ctx / $slots) -lt 4096) {
            Write-Warn2 "per-slot context is small; consider -ContextSize 65536 with -ctk q8_0 -ctv q8_0"
        }
    } catch { }
} catch {
    Write-Warn2 "offline at http://127.0.0.1:$LlamaPort"
    Write-Info "not fatal: DeepSeek handles everything until it starts"
    Write-Info "start it with:  .\scripts\start_llama_server.ps1"
}

# --- 4. backend -------------------------------------------------------------
Write-Head "Backend API (FastAPI)"
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 6
    Write-Ok "online at http://127.0.0.1:$BackendPort"
    Write-Info "deepseek=$($h.deepseek)  minicpm=$($h.minicpm)  database=$($h.database)  max_steps=$($h.details.max_agent_steps)"
    try {
        $s = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/stats" -TimeoutSec 8
        Write-Info "history: $($s.requests) requests, $($s.agent_calls) agent calls, cloud=$($s.cloud_tokens) local=$($s.local_tokens) tokens"
    } catch { }
} catch {
    Write-Bad "not responding on port $BackendPort"
    Write-Info "start it with:  .\scripts\start_backend.ps1"
    Write-Info "or:             .\start.ps1"
}

# --- 5. frontend ------------------------------------------------------------
Write-Head "Frontend dev server (Vite)"
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/" -TimeoutSec 6 -UseBasicParsing
    Write-Ok "online at http://127.0.0.1:$FrontendPort  (HTTP $($r.StatusCode))"
    try {
        $a = Invoke-RestMethod -Uri "http://127.0.0.1:$FrontendPort/api/agents" -TimeoutSec 8
        Write-Ok "dev proxy -> backend works ($($a.Count) agents)"
    } catch {
        Write-Warn2 "page loads but /api proxy is failing - is the backend up?"
    }
} catch {
    Write-Bad "not responding on port $FrontendPort"
    Write-Info "start it with:  .\scripts\start_frontend.ps1"
}

# --- 6. database ------------------------------------------------------------
Write-Head "Database"
$db = Join-Path $projectRoot 'data\multiagent.db'
if (Test-Path $db) {
    $size = [Math]::Round((Get-Item $db).Length / 1KB, 1)
    Write-Ok "data\multiagent.db  ($size KB)"
} else {
    Write-Warn2 "data\multiagent.db not created yet (created on first backend start)"
}

# --- summary ----------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 68) -ForegroundColor DarkGray
Write-Host " Ready to use:  http://127.0.0.1:$FrontendPort" -ForegroundColor Cyan
Write-Host " Start all:     .\start.ps1" -ForegroundColor DarkGray
Write-Host " API docs:      http://127.0.0.1:$BackendPort/docs" -ForegroundColor DarkGray
Write-Host ""
