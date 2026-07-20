[CmdletBinding()]
param(
    [string]$PythonLauncher = "py",
    [string]$PythonVersion = "3.11",
    [switch]$Developer
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$configExample = Join-Path $projectRoot "config.example.json"
$configLocal = Join-Path $projectRoot "config.local.json"

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $PythonLauncher "-$PythonVersion" -m venv $venvPath
}

& $venvPython -m pip install --upgrade pip
Push-Location -LiteralPath $projectRoot
try {
    if ($Developer) {
        & $venvPython -m pip install -e ".[windows,dev]"
    }
    else {
        & $venvPython -m pip install ".[windows]"
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $configLocal)) {
    Copy-Item -LiteralPath $configExample -Destination $configLocal
}

Write-Host "AUTOComp worker installed in $venvPath"
Write-Host "Edit $configLocal, then run scripts\start-worker.ps1"
