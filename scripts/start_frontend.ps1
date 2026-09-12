# ============================================================================
#  Starts the Vite dev server for the frontend.
#
#  Usage (from the project root):
#      .\scripts\start_frontend.ps1
#      .\scripts\start_frontend.ps1 -Port 5174
# ============================================================================

[CmdletBinding()]
param(
    [int]$Port = 5173,
    [string]$BindHost = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $projectRoot 'frontend'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }
function Write-Ok($message)   { Write-Host "OK  $message" -ForegroundColor Green }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Warn "Node.js was not found on PATH. Install Node 18+ and retry."
    exit 1
}
Write-Ok "node $(node --version)"

if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
    Write-Step "Installing frontend dependencies (first run)"
    Push-Location $frontendDir
    try {
        # The project-local .npmrc keeps npm's cache inside the repo, which
        # avoids permission problems with a machine-global cache.
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    } finally {
        Pop-Location
    }
}
Write-Ok "Dependencies present"

# The backend must be reachable for /api to proxy correctly.
Write-Step "Checking the backend"
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 4
    Write-Ok "Backend online (deepseek=$($health.deepseek), minicpm=$($health.minicpm))"
} catch {
    Write-Warn "No backend at http://127.0.0.1:8000 — the UI will load but API calls will fail."
    Write-Host "    Start it in another terminal:" -ForegroundColor DarkGray
    Write-Host "        .\scripts\start_backend.ps1" -ForegroundColor DarkGray
}

Write-Step "Starting Vite on http://${BindHost}:${Port}"
Write-Host "    Open: http://${BindHost}:${Port}" -ForegroundColor DarkGray
Write-Host "    Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

Push-Location $frontendDir
try {
    npm run dev -- --host $BindHost --port $Port
} finally {
    Pop-Location
}
