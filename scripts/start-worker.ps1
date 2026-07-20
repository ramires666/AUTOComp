[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$workerExe = Join-Path $projectRoot ".venv\Scripts\autocomp.exe"

if (-not (Test-Path -LiteralPath $workerExe)) {
    throw "AUTOComp is not installed. Run scripts\install-worker.ps1 first."
}
if ([string]::IsNullOrWhiteSpace($env:AUTOCOMP_WORKER_TOKEN)) {
    throw "Set AUTOCOMP_WORKER_TOKEN to a random token of at least 32 characters."
}
if ($env:AUTOCOMP_WORKER_TOKEN.Length -lt 32) {
    throw "AUTOCOMP_WORKER_TOKEN must contain at least 32 characters."
}
if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $projectRoot "config.local.json"
}

& $workerExe worker-serve --config $Config --port $Port
