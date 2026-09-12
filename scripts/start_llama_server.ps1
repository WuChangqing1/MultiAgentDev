# ============================================================================
#  Starts llama-server for the local MiniCPM5-2B worker model.
#
#  This script NEVER kills or restarts a llama-server you already have running:
#  if the endpoint already answers, it exits and leaves it alone.
#
#  Usage (from the project root):
#      .\scripts\start_llama_server.ps1
#      .\scripts\start_llama_server.ps1 -ContextSize 32768 -GpuLayers 99
# ============================================================================

[CmdletBinding()]
param(
    [string]$LlamaCppDir = 'D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp',
    [string]$ModelPath   = 'D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp\models\MiniCPM5-2B\MiniCPM5-2B-Q8_0.gguf',
    [int]$Port           = 8080,
    [int]$ContextSize    = 16384,
    [int]$GpuLayers      = 99,
    [string]$Alias       = 'MiniCPM5-2B'
)

$ErrorActionPreference = 'Stop'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }
function Write-Ok($message)   { Write-Host "OK  $message" -ForegroundColor Green }

# --- Respect an existing server ---------------------------------------------
Write-Step "Checking http://127.0.0.1:$Port"
try {
    $models = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 4
    $ids = ($models.data | ForEach-Object { $_.id }) -join ', '
    Write-Ok "A server is already running and serving: $ids"
    Write-Host "    Leaving it untouched. Nothing to do." -ForegroundColor DarkGray
    exit 0
} catch {
    Write-Host "    Nothing listening on port $Port - starting a new server." -ForegroundColor DarkGray
}

# --- Validate paths ---------------------------------------------------------
$server = Join-Path $LlamaCppDir 'llama-server.exe'
if (-not (Test-Path $server)) {
    Write-Warn "llama-server.exe not found at: $server"
    Write-Host "    Pass the correct directory with -LlamaCppDir." -ForegroundColor DarkGray
    exit 1
}
if (-not (Test-Path $ModelPath)) {
    Write-Warn "Model file not found at: $ModelPath"
    Write-Host "    Pass the correct file with -ModelPath." -ForegroundColor DarkGray
    exit 1
}

# --- Start ------------------------------------------------------------------
Write-Step "Starting llama-server"
Write-Host "    model      : $ModelPath" -ForegroundColor DarkGray
Write-Host "    context    : $ContextSize tokens" -ForegroundColor DarkGray
Write-Host "    gpu layers : $GpuLayers" -ForegroundColor DarkGray
Write-Host "    endpoint   : http://127.0.0.1:$Port/v1" -ForegroundColor DarkGray
Write-Host "    Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $server `
    --model $ModelPath `
    --alias $Alias `
    --host 127.0.0.1 `
    --port $Port `
    --ctx-size $ContextSize `
    --n-gpu-layers $GpuLayers `
    --jinja
