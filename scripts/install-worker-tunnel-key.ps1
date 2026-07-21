[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PublicKeyFile,

    [ValidateRange(1, 65535)]
    [int]$WorkerPort = 8765,

    [switch]$AdministratorAccount,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$Checkpoint,

    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$resolvedKeyFile = (Resolve-Path -LiteralPath $PublicKeyFile).Path
$keyLines = @(
    [IO.File]::ReadAllLines($resolvedKeyFile) |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
if ($keyLines.Count -ne 1) {
    throw "PublicKeyFile must contain exactly one non-empty public key line."
}

$parts = $keyLines[0] -split '\s+', 3
if ($parts.Count -lt 2 -or
    $parts[0] -notmatch '^ssh-(ed25519|rsa)$|^ecdsa-sha2-') {
    throw "PublicKeyFile must contain a plain OpenSSH public key without options."
}
try {
    [void][Convert]::FromBase64String($parts[1])
}
catch {
    throw "PublicKeyFile contains invalid OpenSSH key data."
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$authorizedKeys = if ($AdministratorAccount) {
    Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
}
else {
    Join-Path $env:USERPROFILE ".ssh\authorized_keys"
}
$requiredPrefix = (
    "restrict,port-forwarding,permitopen=`"127.0.0.1:$WorkerPort`" " +
    "$($parts[0]) $($parts[1])"
)
$entry = "$requiredPrefix autocomp-worker-$Checkpoint"

if (-not $Apply) {
    Write-Host "DRY RUN: no authorized_keys file was changed."
    Write-Host "Target: $authorizedKeys"
    Write-Host "The key will allow only SSH forwarding to 127.0.0.1:$WorkerPort."
    Write-Host "It will not allow a shell, command execution, PTY, agent forwarding, or X11 forwarding."
    Write-Host "Run again with -Apply after checking the target account and worker port."
    exit 0
}

if ($AdministratorAccount) {
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw "-AdministratorAccount -Apply requires an elevated PowerShell window."
    }
}

$authorizedKeysDirectory = Split-Path -Parent $authorizedKeys
if (-not (Test-Path -LiteralPath $authorizedKeysDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $authorizedKeysDirectory | Out-Null
}
if (-not (Test-Path -LiteralPath $authorizedKeys -PathType Leaf)) {
    [IO.File]::WriteAllText($authorizedKeys, "", [Text.UTF8Encoding]::new($false))
}

$keyPattern = [regex]::Escape($parts[0]) + '\s+' + [regex]::Escape($parts[1]) + '(?:\s|$)'
$matchingLines = @(
    [IO.File]::ReadAllLines($authorizedKeys) |
        Where-Object { $_ -match $keyPattern }
)
if ($matchingLines.Count -gt 0 -and
    @($matchingLines | Where-Object { $_.Trim().StartsWith($requiredPrefix) }).Count -eq 0) {
    throw (
        "This public key already exists without the required tunnel-only restrictions. " +
        "Use a new dedicated key or remove the old entry manually after reviewing it."
    )
}
if ($matchingLines.Count -eq 0) {
    [IO.File]::AppendAllText(
        $authorizedKeys,
        $entry + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
}

$currentSid = $identity.User.Value
if ($AdministratorAccount) {
    & icacls.exe $authorizedKeys "/inheritance:r" `
        "/grant:r" "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null
}
else {
    & icacls.exe $authorizedKeys "/inheritance:r" `
        "/grant:r" "*${currentSid}:F" "*S-1-5-18:F" | Out-Null
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restrict authorized_keys permissions with icacls.exe."
}

Write-Host "Installed a tunnel-only key in $authorizedKeys"
Write-Host "Allowed destination: 127.0.0.1:$WorkerPort"
