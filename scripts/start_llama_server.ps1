# ============================================================================
#  Starts llama-server for the local MiniCPM5-2B worker model.
#
#  This script NEVER kills or restarts a llama-server you already have running:
#  if the endpoint already answers, it exits and leaves it alone.
#
#  Usage (from the project root):
#      .\scripts\start_llama_server.ps1
#      .\scripts\start_llama_server.ps1 -ContextSize 65536 -Parallel 4 -QuantizeKv $true
#      .\scripts\start_llama_server.ps1 -ContextSize 16384 -Parallel 1
#
#  VRAM budget (RTX 5070 Laptop 8GB, ~5.5 GB usable while other apps run):
#      KV cache is shared across all slots, so per-slot context is
#      ContextSize / Parallel. Raising ContextSize costs VRAM linearly, and
#      fp16 KV at 64K context is roughly 2.3 GB on top of the weights.
#      When VRAM is tight, either quantize the KV cache (-QuantizeKv) or
#      reduce -Parallel; both cut the KV footprint roughly in half / linearly.
# ============================================================================

[CmdletBinding()]
param(
    [string]$LlamaCppDir = 'D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp',
    [string]$ModelPath   = 'D:\App\Entertainment\AIGC\AIGirlFriend\llama.cpp\models\MiniCPM5-2B\MiniCPM5-2B-Q8_0.gguf',
    [int]$Port           = 8080,
    [int]$ContextSize    = 32768,
    [int]$Parallel       = 4,
    [bool]$QuantizeKv    = $true,
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
$perSlot = [Math]::Floor($ContextSize / [Math]::Max($Parallel, 1))
Write-Step "Starting llama-server"
Write-Host "    model      : $ModelPath" -ForegroundColor DarkGray
Write-Host "    context    : $ContextSize tokens total  ($perSlot per slot across $Parallel slots)" -ForegroundColor DarkGray
Write-Host "    kv cache   : $(if ($QuantizeKv) { 'q8_0 (about half the VRAM of fp16)' } else { 'fp16' })" -ForegroundColor DarkGray
Write-Host "    gpu layers : $GpuLayers" -ForegroundColor DarkGray
Write-Host "    endpoint   : http://127.0.0.1:$Port/v1" -ForegroundColor DarkGray
Write-Host "    Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

# -ctk / -ctv quantize the KV cache. Without them, a large -ContextSize can
# exceed the 8 GB card and the server exits with an out-of-memory error.
$kvArgs = if ($QuantizeKv) { @('-ctk', 'q8_0', '-ctv', 'q8_0') } else { @() }

& $server `
    --model $ModelPath `
    --alias $Alias `
    --host 127.0.0.1 `
    --port $Port `
    --ctx-size $ContextSize `
    --parallel $Parallel `
    --n-gpu-layers $GpuLayers `
    @kvArgs `
    --jinja

# --- Post-start guidance ----------------------------------------------------
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Warn "llama-server exited with code $LASTEXITCODE."
    Write-Host "    If it failed to allocate VRAM, try one of:" -ForegroundColor DarkGray
    Write-Host "      .\scripts\start_llama_server.ps1 -ContextSize 32768 -Parallel 2" -ForegroundColor DarkGray
    Write-Host "      .\scripts\start_llama_server.ps1 -ContextSize 32768 -QuantizeKv `$true" -ForegroundColor DarkGray
    Write-Host "    Check free VRAM with:  nvidia-smi" -ForegroundColor DarkGray
}
