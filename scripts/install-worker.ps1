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
$envExample = Join-Path $projectRoot ".env.example"
$envLocal = Join-Path $projectRoot ".env"

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
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip in $venvPath."
}
Push-Location -LiteralPath $projectRoot
try {
    if ($Developer) {
        & $venvPython -m pip install -e ".[windows,dev]"
    }
    else {
        & $venvPython -m pip install ".[windows]"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install AUTOComp dependencies."
    }
}
finally {
    Pop-Location
}

& $venvPython -c "from PIL import ImageGrab; import PIL; print(f'Pillow {PIL.__version__} OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Pillow/ImageGrab is unavailable in $venvPath. Re-run this installer after checking Python and pip output."
}

if (-not (Test-Path -LiteralPath $configLocal)) {
    Copy-Item -LiteralPath $configExample -Destination $configLocal
}
if (-not (Test-Path -LiteralPath $envLocal)) {
    Copy-Item -LiteralPath $envExample -Destination $envLocal
}
$envText = [IO.File]::ReadAllText($envLocal)
$workerTokenMatch = [regex]::Match($envText, "(?m)^AUTOCOMP_WORKER_TOKEN=(.*)$")
if (-not $workerTokenMatch.Success) {
    throw "AUTOCOMP_WORKER_TOKEN entry is missing from $envLocal"
}
if ([string]::IsNullOrWhiteSpace($workerTokenMatch.Groups[1].Value)) {
    $tokenBytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($tokenBytes)
    }
    finally {
        $random.Dispose()
    }
    $workerToken = [Convert]::ToBase64String($tokenBytes)
    $envText = [regex]::Replace(
        $envText,
        "(?m)^AUTOCOMP_WORKER_TOKEN=.*$",
        "AUTOCOMP_WORKER_TOKEN=$workerToken"
    )
    [IO.File]::WriteAllText($envLocal, $envText, [Text.UTF8Encoding]::new($false))
}

Write-Host "AUTOComp worker installed in $venvPath"
Write-Host "Edit $envLocal and $configLocal, then run scripts\start-worker.ps1"
