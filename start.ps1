# ============================================================================
#  One-command launcher.
#
#  Verifies the environment, then starts the backend and the frontend in two
#  separate PowerShell windows so you can watch both logs.
#
#  This script deliberately does NOT start or stop llama-server. It only checks
#  whether http://127.0.0.1:8080 is answering -- see requirement #54.
#
#  Usage (from the project root):
#      .\start.ps1
#      .\start.ps1 -BackendPort 8001 -FrontendPort 5174
#      .\start.ps1 -NoFrontend
# ============================================================================

[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$CondaEnvName = 'MultiAgent',
    [switch]$NoFrontend,
    [switch]$InstallDeps
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }
function Write-Ok($message)   { Write-Host "OK  $message" -ForegroundColor Green }
function Write-Head($message) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor DarkGray
    Write-Host " $message" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor DarkGray
}

Write-Head "MultiAgent — DeepSeek MainAgent x local MiniCPM5-2B"

# --- 1. Locate the conda environment ----------------------------------------
Write-Step "Conda environment '$CondaEnvName'"
$python = $null
if ($env:CONDA_DEFAULT_ENV -eq $CondaEnvName -and $env:CONDA_PREFIX) {
    $candidate = Join-Path $env:CONDA_PREFIX 'python.exe'
    if (Test-Path $candidate) { $python = $candidate }
}
if (-not $python) {
    foreach ($root in @(
        "$env:USERPROFILE\miniconda3", "$env:USERPROFILE\anaconda3",
        'C:\ProgramData\miniconda3', 'C:\ProgramData\Anaconda3',
        'D:\App\Business\Coding\Python\Miniconda'
    )) {
        $candidate = Join-Path $root "envs\$CondaEnvName\python.exe"
        if (Test-Path $candidate) { $python = $candidate; break }
    }
}
if (-not $python) {
    Write-Warn "Environment '$CondaEnvName' not found."
    Write-Host "    Create it, then re-run:  conda create -n $CondaEnvName python=3.12" -ForegroundColor DarkGray
    exit 1
}
Write-Ok $python

# --- 2. Backend dependencies ------------------------------------------------
Write-Step "Backend dependencies"
$probe = & $python -c "import fastapi, uvicorn, openai, sqlalchemy, aiosqlite; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0 -or $probe -notmatch 'ok') {
    if ($InstallDeps) {
        Write-Step "Installing backend dependencies (this may take a minute)"
        & $python -m pip install -r (Join-Path $projectRoot 'backend\requirements.txt')
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Install failed. If PyPI is unreachable, retry against a mirror:"
            Write-Host "    python -m pip install -r backend\requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple" -ForegroundColor DarkGray
            exit 1
        }
    } else {
        Write-Warn "Missing. Re-run with -InstallDeps, or install manually:"
        Write-Host "    conda activate $CondaEnvName" -ForegroundColor DarkGray
        Write-Host "    python -m pip install -r backend\requirements.txt" -ForegroundColor DarkGray
        exit 1
    }
}
Write-Ok "present"

# --- 3. Frontend dependencies -----------------------------------------------
if (-not $NoFrontend) {
    Write-Step "Frontend dependencies"
    if (-not (Test-Path (Join-Path $projectRoot 'frontend\node_modules'))) {
        Write-Step "Running npm install (first run)"
        Push-Location (Join-Path $projectRoot 'frontend')
        try {
            npm install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
        } finally { Pop-Location }
    }
    Write-Ok "present"
}

# --- 4. .env ----------------------------------------------------------------
Write-Step ".env"
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $projectRoot '.env.example') $envFile
    Write-Warn "Created .env from .env.example."
    Write-Warn "Set DEEPSEEK_API_KEY in $envFile and re-run."
    exit 1
}
if (-not (Select-String -Path $envFile -Pattern '^\s*DEEPSEEK_API_KEY\s*=\s*\S+' -Quiet)) {
    Write-Warn "DEEPSEEK_API_KEY is empty in .env — the MainAgent needs it."
    exit 1
}
Write-Ok "configured"

# --- 5. Local model (advisory) ----------------------------------------------
Write-Step "Local model at http://127.0.0.1:8080"
$localOk = $false
try {
    $models = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 4
    $ids = ($models.data | ForEach-Object { $_.id }) -join ', '
    Write-Ok "serving: $ids"
    $localOk = $true
} catch {
    Write-Warn "not running. DeepSeek will handle all work (fallback is automatic)."
    Write-Host "    To start it:  .\scripts\start_llama_server.ps1" -ForegroundColor DarkGray
}

# --- 6. Launch --------------------------------------------------------------
Write-Head "Starting services"

$backendScript = Join-Path $projectRoot 'scripts\start_backend.ps1'
$frontendScript = Join-Path $projectRoot 'scripts\start_frontend.ps1'

Write-Step "Backend  -> http://127.0.0.1:$BackendPort"
Start-Process -FilePath 'pwsh' -ArgumentList @(
    '-NoExit', '-File', $backendScript,
    '-Port', $BackendPort, '-CondaEnvName', $CondaEnvName
) | Out-Null

if (-not $NoFrontend) {
    Start-Sleep -Seconds 3
    Write-Step "Frontend -> http://127.0.0.1:$FrontendPort"
    Start-Process -FilePath 'pwsh' -ArgumentList @(
        '-NoExit', '-File', $frontendScript, '-Port', $FrontendPort
    ) | Out-Null
}

Write-Head "Ready"
Write-Host "  Open:        http://127.0.0.1:$FrontendPort" -ForegroundColor Green
Write-Host "  Backend API: http://127.0.0.1:$BackendPort/docs" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Local workers: $(if ($localOk) { 'enabled (MiniCPM online)' } else { 'offline — DeepSeek-only mode' })" -ForegroundColor DarkGray
Write-Host "  Each service runs in its own window; close them to stop." -ForegroundColor DarkGray
Write-Host ""
