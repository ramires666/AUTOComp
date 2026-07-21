[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ClientAddress,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$Checkpoint,

    [switch]$ReplaceBroadRule,

    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$parsedAddress = $null
if (-not [Net.IPAddress]::TryParse($ClientAddress, [ref]$parsedAddress)) {
    throw "ClientAddress must be one explicit IPv4 or IPv6 address, not a hostname or subnet."
}
if ([Net.IPAddress]::IsLoopback($parsedAddress) -or
    $parsedAddress.Equals([Net.IPAddress]::Any) -or
    $parsedAddress.Equals([Net.IPAddress]::IPv6Any)) {
    throw "ClientAddress must be the controlling computer's non-loopback address."
}

$steps = @(
    "Install the Windows OpenSSH Server capability if it is absent",
    "Set the sshd service to Automatic and start it",
    "Inspect the default broad OpenSSH-Server-In-TCP firewall rule",
    "Allow inbound TCP 22 only from $ClientAddress",
    "Record checkpoint $Checkpoint in the firewall rule description"
)

if (-not $Apply) {
    Write-Host "DRY RUN: no Windows settings were changed."
    $steps | ForEach-Object { Write-Host "- $_" }
    Write-Host "Run again with -Apply from an elevated PowerShell window after checking ClientAddress."
    exit 0
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "-Apply requires an elevated PowerShell window (Run as administrator)."
}

$capability = Get-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
$defaultRule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
$enabledBroadRule = @($defaultRule | Where-Object { $_.Enabled -eq "True" }).Count -gt 0
if (($capability.State -ne "Installed" -or $enabledBroadRule) -and -not $ReplaceBroadRule) {
    throw (
        "OpenSSH installation may create, or already has, the broad default firewall rule. " +
        "Review existing SSH access, then rerun with -ReplaceBroadRule to explicitly disable " +
        "that rule and use the source-restricted AUTOComp rule."
    )
}
if ($capability.State -ne "Installed") {
    Write-Host "Installing Windows OpenSSH Server..."
    $installResult = Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
    if ($installResult.RestartNeeded) {
        throw "OpenSSH Server installation requires a restart. Restart, then rerun this command."
    }
}

Set-Service -Name "sshd" -StartupType Automatic
Start-Service -Name "sshd"

$defaultRule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
if ($defaultRule -and $ReplaceBroadRule) {
    $defaultRule | Disable-NetFirewallRule
}

$ruleName = "AUTOComp-SSHD-Restricted"
$description = "AUTOComp SSH tunnel; source $ClientAddress; checkpoint $Checkpoint"
$existingRule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Set-NetFirewallRule `
        -Name $ruleName `
        -Enabled True `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 22 `
        -RemoteAddress $ClientAddress `
        -Description $description | Out-Null
}
else {
    New-NetFirewallRule `
        -Name $ruleName `
        -DisplayName "AUTOComp restricted SSH tunnel" `
        -Description $description `
        -Enabled True `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 22 `
        -RemoteAddress $ClientAddress | Out-Null
}

Write-Host "OpenSSH Server is running. The AUTOComp rule allows TCP 22 only from $ClientAddress."
Write-Host "The AUTOComp worker itself remains on 127.0.0.1 and is not exposed to the LAN."
Write-Host "Rollback: disable firewall rule AUTOComp-SSHD-Restricted; re-enable the default rule only if its former scope is acceptable."
