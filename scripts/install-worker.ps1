[CmdletBinding()]
param(
    [string]$PythonLauncher = "",
    [string]$PythonVersion = "",
    [switch]$Developer
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$configExample = Join-Path $projectRoot "config.example.json"
$configLocal = Join-Path $projectRoot "config.local.json"

function Find-PythonCommand {
    $candidates = if ($PythonLauncher) {
        @($PythonLauncher)
    }
    else {
        @("py", "python", "python3", "python3.14")
    }

    foreach ($candidate in $candidates) {
        $command = Get-Command -Name $candidate -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $command) {
            continue
        }

        $commandPath = $command.Source
        $isLauncher = [IO.Path]::GetFileNameWithoutExtension($command.Name) -eq "py"
        $selectorArgs = if ($isLauncher -and $PythonVersion) {
            @("-$PythonVersion")
        }
        else {
            @()
        }

        $versionText = & $commandPath @selectorArgs -c `
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $versionText) {
            continue
        }

        $version = [version]($versionText | Select-Object -Last 1)
        if ($version -lt [version]"3.11") {
            continue
        }
        if ($PythonVersion -and -not $isLauncher -and
            $version -ne [version]$PythonVersion) {
            continue
        }

        return [pscustomobject]@{
            Path = $commandPath
            Arguments = $selectorArgs
            Version = $version
        }
    }

    $hint = if ($PythonVersion) { " Python $PythonVersion was requested." } else { "" }
    throw "Python 3.11 or newer was not found.$hint Ensure python.exe is in PATH or pass -PythonLauncher with its full path."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $python = Find-PythonCommand
    Write-Host "Using Python $($python.Version) from $($python.Path)"
    $pythonPath = $python.Path
    $pythonArguments = @($python.Arguments)
    & $pythonPath @pythonArguments -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment with $($python.Path)."
    }
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
