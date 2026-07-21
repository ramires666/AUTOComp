[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteHost,

    [string]$RemoteUser = "",

    [ValidateRange(1, 65535)]
    [int]$SshPort = 22,

    [ValidateRange(1, 65535)]
    [int]$LocalPort = 8765,

    [ValidateRange(1, 65535)]
    [int]$RemoteWorkerPort = 8765,

    [string]$IdentityFile = ""
)

$ErrorActionPreference = "Stop"

$sshCommand = Get-Command -Name "ssh.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $sshCommand) {
    throw "OpenSSH Client (ssh.exe) was not found on this computer."
}

if ([string]::IsNullOrWhiteSpace($RemoteHost)) {
    throw "RemoteHost must not be empty."
}
if ($RemoteHost -notmatch '^[A-Za-z0-9]' -or $RemoteHost -match '[\s`"'';|&<>]') {
    throw "RemoteHost contains unsupported characters."
}
if ((-not [string]::IsNullOrWhiteSpace($RemoteUser) -and
    $RemoteUser -notmatch '^[A-Za-z0-9]') -or
    $RemoteUser -match '[\s`"'';|&<>@]') {
    throw "RemoteUser contains unsupported characters."
}

$target = if ([string]::IsNullOrWhiteSpace($RemoteUser)) {
    $RemoteHost
}
else {
    "$RemoteUser@$RemoteHost"
}

$sshArguments = @(
    "-N",
    "-T",
    "-p", [string]$SshPort,
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-L", "127.0.0.1:${LocalPort}:127.0.0.1:${RemoteWorkerPort}"
)

if (-not [string]::IsNullOrWhiteSpace($IdentityFile)) {
    $resolvedIdentity = (Resolve-Path -LiteralPath $IdentityFile).Path
    $sshArguments += @("-i", $resolvedIdentity)
}
$sshArguments += $target

Write-Host "Opening AUTOComp worker tunnel on http://127.0.0.1:$LocalPort"
Write-Host "Keep this window open. Press Ctrl+C to close only the tunnel."
& $sshCommand.Source @sshArguments
exit $LASTEXITCODE
