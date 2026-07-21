[CmdletBinding(DefaultParameterSetName = "Health")]
param(
    [Parameter(ParameterSetName = "Health")]
    [switch]$Health,

    [Parameter(Mandatory = $true, ParameterSetName = "Capabilities")]
    [switch]$Capabilities,

    [Parameter(Mandatory = $true, ParameterSetName = "Status")]
    [switch]$Status,

    [Parameter(Mandatory = $true, ParameterSetName = "Inventory")]
    [switch]$Inventory,

    [Parameter(Mandatory = $true, ParameterSetName = "TreeInventory")]
    [switch]$InventoryProjectTree,

    [Parameter(Mandatory = $true, ParameterSetName = "Rename")]
    [switch]$RenameTreeItem,

    [Parameter(Mandatory = $true, ParameterSetName = "ProbeRename")]
    [switch]$ProbeTreeItemRename,

    [Parameter(Mandatory = $true, ParameterSetName = "InspectMenu")]
    [switch]$InspectTreeItemMenu,

    [Parameter(Mandatory = $true, ParameterSetName = "Rename")]
    [Parameter(Mandatory = $true, ParameterSetName = "ProbeRename")]
    [Parameter(Mandatory = $true, ParameterSetName = "InspectMenu")]
    [ValidateCount(1, 64)]
    [ValidateRange(0, 2147483647)]
    [int[]]$Locator,

    [Parameter(Mandatory = $true, ParameterSetName = "Rename")]
    [Parameter(Mandatory = $true, ParameterSetName = "ProbeRename")]
    [Parameter(Mandatory = $true, ParameterSetName = "InspectMenu")]
    [ValidateCount(1, 64)]
    [string[]]$ExpectedPath,

    [Parameter(Mandatory = $true, ParameterSetName = "Rename")]
    [Parameter(Mandatory = $true, ParameterSetName = "ProbeRename")]
    [Parameter(Mandatory = $true, ParameterSetName = "InspectMenu")]
    [string]$ExpectedSource,

    [Parameter(Mandatory = $true, ParameterSetName = "Rename")]
    [Parameter(Mandatory = $true, ParameterSetName = "ProbeRename")]
    [string]$Target,

    [Parameter(ParameterSetName = "TreeInventory")]
    [Parameter(Mandatory = $true, ParameterSetName = "Rename")]
    [Parameter(Mandatory = $true, ParameterSetName = "ProbeRename")]
    [Parameter(Mandatory = $true, ParameterSetName = "InspectMenu")]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$Checkpoint,

    [Parameter(ParameterSetName = "TreeInventory")]
    [Parameter(ParameterSetName = "Rename")]
    [Parameter(ParameterSetName = "ProbeRename")]
    [Parameter(ParameterSetName = "InspectMenu")]
    [switch]$Apply,

    [Parameter(ParameterSetName = "TreeInventory")]
    [switch]$ExpandAll,

    [uri]$Endpoint,
    [switch]$AllowLanHttp,
    [string]$EnvFile = "",
    [ValidateRange(1, 3600)]
    [int]$TimeoutSeconds = 120,
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

function Resolve-EnvironmentFile {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        $Path = Join-Path $projectRoot ".env"
    }
    elseif (-not [IO.Path]::IsPathRooted($Path)) {
        $Path = Join-Path $projectRoot $Path
    }
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return (Resolve-Path -LiteralPath $Path).Path
    }
    return ""
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*?)\s*$'
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match $pattern) {
            $value = $Matches[1]
            if ($value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value
            }
        }
    }
    return ""
}

if (-not [string]::IsNullOrWhiteSpace($EnvFile)) {
    $candidateEnvFile = if ([IO.Path]::IsPathRooted($EnvFile)) {
        $EnvFile
    }
    else {
        Join-Path $projectRoot $EnvFile
    }
    if (-not (Test-Path -LiteralPath $candidateEnvFile -PathType Leaf)) {
        throw "Specified environment file does not exist: $candidateEnvFile"
    }
}
$resolvedEnvFile = Resolve-EnvironmentFile -Path $EnvFile
$token = $env:AUTOCOMP_WORKER_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    $token = Get-DotEnvValue -Path $resolvedEnvFile -Name "AUTOCOMP_WORKER_TOKEN"
}
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "AUTOCOMP_WORKER_TOKEN is missing from the process environment and env file."
}

if (-not $Endpoint) {
    $endpointText = $env:AUTOCOMP_WORKER_ENDPOINT
    if ([string]::IsNullOrWhiteSpace($endpointText)) {
        $endpointText = Get-DotEnvValue `
            -Path $resolvedEnvFile `
            -Name "AUTOCOMP_WORKER_ENDPOINT"
    }
    if ([string]::IsNullOrWhiteSpace($endpointText)) {
        $endpointText = "http://127.0.0.1:8765"
    }
    $Endpoint = [uri]$endpointText
}

if (-not $Endpoint.IsAbsoluteUri -or
    ($Endpoint.Scheme -ne "http" -and $Endpoint.Scheme -ne "https")) {
    throw "Endpoint must use http or https."
}
if (-not [string]::IsNullOrEmpty($Endpoint.UserInfo) -or
    -not [string]::IsNullOrEmpty($Endpoint.Query) -or
    -not [string]::IsNullOrEmpty($Endpoint.Fragment)) {
    throw "Endpoint must not contain credentials, a query, or a fragment."
}
if ($Endpoint.Scheme -eq "http" -and
    $Endpoint.Host -notin @("127.0.0.1", "localhost", "::1") -and
    -not $AllowLanHttp) {
    throw "Plain LAN HTTP requires the explicit -AllowLanHttp switch."
}
if (-not [string]::IsNullOrEmpty($Endpoint.AbsolutePath.Trim('/'))) {
    throw "Endpoint must be a server root such as http://127.0.0.1:8765."
}

$headers = @{ Authorization = "Bearer $token" }
$baseUri = $Endpoint.AbsoluteUri.TrimEnd('/')

if ($PSCmdlet.ParameterSetName -in @("Health", "Capabilities", "Status")) {
    $path = switch ($PSCmdlet.ParameterSetName) {
        "Health" { "/health" }
        "Capabilities" { "/v1/capabilities" }
        "Status" { "/v1/status" }
    }
    $result = Invoke-RestMethod `
        -Method Get `
        -Uri "$baseUri$path" `
        -Headers $headers `
        -TimeoutSec $TimeoutSeconds
}
else {
    if ($PSCmdlet.ParameterSetName -eq "Inventory") {
        $payload = [ordered]@{ action = "inventory" }
    }
    elseif ($PSCmdlet.ParameterSetName -eq "TreeInventory") {
        if ($ExpandAll -and (-not $Apply -or [string]::IsNullOrWhiteSpace($Checkpoint))) {
            throw "ExpandAll requires both -Apply and a non-empty -Checkpoint."
        }
        if ($Apply -and [string]::IsNullOrWhiteSpace($Checkpoint)) {
            throw "Apply requires a non-empty -Checkpoint."
        }
        $payload = [ordered]@{
            action = "inventory_project_tree"
            expand_all = [bool]$ExpandAll
            restore_state = $true
            apply = [bool]$Apply
        }
        if (-not [string]::IsNullOrWhiteSpace($Checkpoint)) {
            $payload.checkpoint = $Checkpoint
        }
    }
    elseif ($PSCmdlet.ParameterSetName -eq "InspectMenu") {
        $invalidExpectedPath = @(
            $ExpectedPath | Where-Object { [string]::IsNullOrWhiteSpace($_) }
        ).Count -gt 0
        if (-not $Locator -or
            $ExpectedPath.Count -ne $Locator.Count -or
            $invalidExpectedPath -or
            [string]::IsNullOrWhiteSpace($ExpectedSource) -or
            [string]::IsNullOrWhiteSpace($Checkpoint) -or
            -not $Apply) {
            throw "InspectMenu requires exact node identity, -Apply, and -Checkpoint."
        }
        if ($ExpectedPath[-1] -cne $ExpectedSource) {
            throw "The last ExpectedPath element must exactly equal ExpectedSource."
        }
        $payload = [ordered]@{
            action = "inspect_tree_item_menu"
            locator = $Locator
            expected_path = $ExpectedPath
            expected_source = $ExpectedSource
            checkpoint = $Checkpoint
            apply = $true
        }
    }
    else {
        $invalidExpectedPath = @(
            $ExpectedPath | Where-Object { [string]::IsNullOrWhiteSpace($_) }
        ).Count -gt 0
        if (-not $Locator -or
            $ExpectedPath.Count -ne $Locator.Count -or
            $invalidExpectedPath -or
            [string]::IsNullOrWhiteSpace($ExpectedSource) -or
            [string]::IsNullOrWhiteSpace($Target) -or
            [string]::IsNullOrWhiteSpace($Checkpoint)) {
            throw "Rename parameters must not be empty."
        }
        if ($ExpectedPath[-1] -cne $ExpectedSource) {
            throw "The last ExpectedPath element must exactly equal ExpectedSource."
        }
        if ($ExpectedSource -ceq $Target) {
            throw "ExpectedSource and Target must differ."
        }
        $payload = [ordered]@{
            action = if ($PSCmdlet.ParameterSetName -eq "ProbeRename") {
                "probe_tree_item_rename"
            }
            else {
                "rename_tree_item"
            }
            locator = $Locator
            expected_path = $ExpectedPath
            expected_source = $ExpectedSource
            target = $Target
            checkpoint = $Checkpoint
            apply = [bool]$Apply
        }
    }

    $result = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUri/v1/action" `
        -Headers $headers `
        -ContentType "application/json; charset=utf-8" `
        -Body ($payload | ConvertTo-Json -Compress) `
        -TimeoutSec $TimeoutSeconds
}

$json = $result | ConvertTo-Json -Depth 20
if ([string]::IsNullOrWhiteSpace($Output)) {
    $json
}
else {
    if (-not [IO.Path]::IsPathRooted($Output)) {
        $Output = Join-Path $projectRoot $Output
    }
    $outputPath = [IO.Path]::GetFullPath($Output)
    $outputDirectory = Split-Path -Parent $outputPath
    if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $outputDirectory | Out-Null
    }
    $stream = [IO.File]::Open($outputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $writer = New-Object IO.StreamWriter($stream, [Text.UTF8Encoding]::new($false))
        try {
            $writer.WriteLine($json)
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
    Write-Host "Worker response written to $outputPath"
}
