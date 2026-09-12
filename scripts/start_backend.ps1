# ============================================================================
#  Starts the MultiAgent backend in the "MultiAgent" conda environment.
#
#  Usage (from the project root):
#      .\scripts\start_backend.ps1
#      .\scripts\start_backend.ps1 -Port 8001
#      .\scripts\start_backend.ps1 -NoReload
# ============================================================================

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = '127.0.0.1',
    [string]$CondaEnvName = 'MultiAgent',
    [switch]$NoReload
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }
function Write-Ok($message)   { Write-Host "OK  $message" -ForegroundColor Green }

# --- Locate the conda environment -------------------------------------------
Write-Step "Locating the '$CondaEnvName' conda environment"

$python = $null

# 1. Already inside the right environment?
if ($env:CONDA_DEFAULT_ENV -eq $CondaEnvName -and $env:CONDA_PREFIX) {
    $candidate = Join-Path $env:CONDA_PREFIX 'python.exe'
    if (Test-Path $candidate) { $python = $candidate }
}

# 2. Ask conda itself (plugins disabled: some builds crash on `conda info`).
if (-not $python) {
    $condaBase = (Get-Command conda -ErrorAction SilentlyContinue).Source
    if ($condaBase) {
        $condaRoot = Split-Path -Parent (Split-Path -Parent $condaBase)
        $candidate = Join-Path $condaRoot "envs\$CondaEnvName\python.exe"
        if (Test-Path $candidate) { $python = $candidate }
    }
}

# 3. Fall back to the default Miniconda locations.
if (-not $python) {
    foreach ($root in @(
        "$env:USERPROFILE\miniconda3",
        "$env:USERPROFILE\anaconda3",
        'C:\ProgramData\miniconda3',
        'C:\ProgramData\Anaconda3',
        'D:\App\Business\Coding\Python\Miniconda'
    )) {
        $candidate = Join-Path $root "envs\$CondaEnvName\python.exe"
        if (Test-Path $candidate) { $python = $candidate; break }
    }
}

if (-not $python) {
    Write-Warn "Could not find the '$CondaEnvName' conda environment automatically."
    Write-Host "    Create/activate it, then re-run this script:" -ForegroundColor DarkGray
    Write-Host "        conda activate $CondaEnvName" -ForegroundColor DarkGray
    exit 1
}
Write-Ok "Python: $python"

# --- Verify dependencies ----------------------------------------------------
Write-Step "Checking backend dependencies"
$probe = & $python -c "import fastapi, uvicorn, openai, sqlalchemy, aiosqlite, pydantic_settings; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0 -or $probe -notmatch 'ok') {
    Write-Warn "Dependencies are missing from '$CondaEnvName'."
    Write-Host "    Install them with:" -ForegroundColor DarkGray
    Write-Host "        conda activate $CondaEnvName" -ForegroundColor DarkGray
    Write-Host "        python -m pip install -r backend\requirements.txt" -ForegroundColor DarkGray
    Write-Host "    (if PyPI is unreachable, add: --index-url https://pypi.tuna.tsinghua.edu.cn/simple)" -ForegroundColor DarkGray
    exit 1
}
Write-Ok "Dependencies present"

# --- Check .env -------------------------------------------------------------
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path $envFile)) {
    Write-Warn ".env not found. Creating it from .env.example."
    Copy-Item (Join-Path $projectRoot '.env.example') $envFile
    Write-Warn "Set DEEPSEEK_API_KEY in $envFile, then re-run this script."
    exit 1
}
if (-not (Select-String -Path $envFile -Pattern '^\s*DEEPSEEK_API_KEY\s*=\s*\S+' -Quiet)) {
    Write-Warn "DEEPSEEK_API_KEY is empty in .env — the MainAgent cannot run without it."
}
Write-Ok ".env present"

# --- Check the local model (advisory only, must never block startup) --------
Write-Step "Checking the local MiniCPM endpoint"
try {
    $models = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 4
    $ids = ($models.data | ForEach-Object { $_.id }) -join ', '
    Write-Ok "llama-server is serving: $ids"
} catch {
    Write-Warn "No llama-server at http://127.0.0.1:8080 — starting anyway."
    Write-Host "    DeepSeek will handle everything until it is running. Start it with:" -ForegroundColor DarkGray
    Write-Host "        .\scripts\start_llama_server.ps1" -ForegroundColor DarkGray
}

# --- Launch -----------------------------------------------------------------
Write-Step "Starting FastAPI on http://${BindHost}:${Port}"
Write-Host "    Docs:   http://${BindHost}:${Port}/docs" -ForegroundColor DarkGray
Write-Host "    Health: http://${BindHost}:${Port}/api/health" -ForegroundColor DarkGray
Write-Host "    Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

$uvicornArgs = @('-m', 'uvicorn', 'main:app', '--host', $BindHost, '--port', "$Port")
if (-not $NoReload) { $uvicornArgs += '--reload' }

Push-Location $backendDir
try {
    & $python @uvicornArgs
} finally {
    Pop-Location
}
