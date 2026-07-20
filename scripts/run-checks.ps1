[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$ruffExe = Join-Path $projectRoot ".venv\Scripts\ruff.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Development environment is missing. Run scripts\install-worker.ps1 -Developer first."
}

if (-not (Test-Path -LiteralPath $ruffExe)) {
    throw "Ruff is missing. Run scripts\install-worker.ps1 -Developer first."
}

& $ruffExe check $projectRoot
& $venvPython -m pytest -q --basetemp (Join-Path $projectRoot ".test-tmp")
