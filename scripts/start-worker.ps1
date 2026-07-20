[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string]$Config = "",
    [string]$EnvFile = ""
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

$workerArguments = @("worker-serve", "--config", $Config, "--port", $Port)
if (-not [string]::IsNullOrWhiteSpace($EnvFile)) {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "Specified environment file does not exist: $EnvFile"
    }
    $workerArguments += @("--env-file", $EnvFile)
}

& $workerExe @workerArguments
exit $LASTEXITCODE
