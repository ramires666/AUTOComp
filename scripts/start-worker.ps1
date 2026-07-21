[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [ValidatePattern('^(?:\d{1,3}\.){3}\d{1,3}$|^::1$|^::$')]
    [string]$ListenAddress = "127.0.0.1",
    [switch]$AllowRemote,
    [string]$Config = "",
    [string]$EnvFile = "",
    [string]$AuditLog = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$workerExe = Join-Path $projectRoot ".venv\Scripts\autocomp.exe"

if (-not (Test-Path -LiteralPath $workerExe)) {
    throw "AUTOComp is not installed. Run scripts\install-worker.ps1 first."
}
if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $projectRoot "config.local.json"
}
elseif (-not [IO.Path]::IsPathRooted($Config)) {
    $Config = Join-Path $projectRoot $Config
}
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $candidateEnvFile = Join-Path (Split-Path -Parent $Config) ".env"
    if (Test-Path -LiteralPath $candidateEnvFile) {
        $EnvFile = $candidateEnvFile
    }
}
elseif (-not [IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile = Join-Path $projectRoot $EnvFile
}

if ([string]::IsNullOrWhiteSpace($AuditLog)) {
    $AuditLog = Join-Path $projectRoot ".autocomp\worker-audit.jsonl"
}
elseif (-not [IO.Path]::IsPathRooted($AuditLog)) {
    $AuditLog = Join-Path $projectRoot $AuditLog
}
$auditDirectory = Split-Path -Parent $AuditLog
if (-not (Test-Path -LiteralPath $auditDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $auditDirectory | Out-Null
}

$workerArguments = @(
    "worker-serve",
    "--config", $Config,
    "--host", $ListenAddress,
    "--port", $Port,
    "--audit-log", $AuditLog
)
if ($AllowRemote) {
    $workerArguments += "--allow-remote"
}
if (-not [string]::IsNullOrWhiteSpace($EnvFile)) {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "Specified environment file does not exist: $EnvFile"
    }
    $workerArguments += @("--env-file", $EnvFile)
}

Write-Host "Starting AUTOComp worker in this logged-in Windows session."
Write-Host "KV STUDIO may be minimized for UI Automation inventory; visual fallback requires it to be visible."
if ($AllowRemote) {
    Write-Host "LAN/VM worker enabled at http://${ListenAddress}:$Port (bearer token required)."
}
else {
    Write-Host "The worker stays bound to 127.0.0.1."
}
& $workerExe @workerArguments
exit $LASTEXITCODE
