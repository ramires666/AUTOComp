# Remote KV STUDIO worker

This setup controls only the allowlisted AUTOComp/KV STUDIO operations on the
dedicated Windows computer. It deliberately does not provide a remote shell,
arbitrary mouse/keyboard input, arbitrary file access, PLC access, or generic
desktop control.

The worker can listen directly on a trusted LAN/VMware interface or remain on
loopback behind an SSH tunnel. The bearer token remains in an ignored `.env`
file and is never placed in a URL or command-line argument.

## Simple LAN or VMware setup

For a trusted local network, VMware host-only network, or NAT network with no
port forwarding, SSH is optional. Start the worker directly on the KV/VM
computer:

```powershell
cd C:\projects\AUTOComp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start-worker.ps1 `
  -ListenAddress 0.0.0.0 `
  -AllowRemote
```

Put the VM/remote-computer address and the same worker token in the controller's
ignored `.env.remote` file:

```dotenv
AUTOCOMP_WORKER_ENDPOINT=http://192.168.56.101:8765
AUTOCOMP_WORKER_TOKEN=the-same-random-token-from-the-KV-computer
```

Then connect directly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -Health -AllowLanHttp -EnvFile .env.remote
```

Use `-AllowLanHttp` on every controller command in this direct mode. The bearer
token remains mandatory, but OpenSSH keys and tunnel setup below are unnecessary.
Use the guest's host-only/NAT IP; do not configure router port forwarding.

## Optional SSH tunnel setup

### One-time setup on the KV computer

Open an elevated PowerShell window through RDP. Determine the controlling
computer's fixed LAN/VPN address, then preview the changes:

```powershell
cd C:\projects\AUTOComp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\configure-worker-ssh.ps1 `
  -ClientAddress 192.168.1.20 `
  -Checkpoint remote-worker-setup
```

If the displayed address and operations are correct, repeat with `-Apply`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\configure-worker-ssh.ps1 `
  -ClientAddress 192.168.1.20 `
  -Checkpoint remote-worker-setup `
  -ReplaceBroadRule `
  -Apply
```

This installs Windows OpenSSH Server when necessary, starts `sshd`, explicitly
replaces the default broad OpenSSH firewall rule, and creates an inbound rule
restricted to that one controller address. Omitting `-ReplaceBroadRule` makes
the script stop instead of silently changing existing SSH access. It does not
expose ports 8765 or 8080.

Install AUTOComp from a normal, non-elevated PowerShell window:

```powershell
cd C:\projects\AUTOComp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install-worker.ps1
```

The installer discovers Python 3.11 or newer, including Python 3.14 without the
legacy `py` launcher. It creates `.venv`, copies local configuration templates,
and generates a random worker token if the token is empty.

### Tunnel-only SSH identity

Generate a dedicated key on the controlling computer. Do not reuse a general
administration key:

```powershell
ssh-keygen.exe -t ed25519 -f "$env:USERPROFILE\.ssh\autocomp-worker" -C autocomp-worker
```

Transfer only `autocomp-worker.pub` to the KV computer through the existing RDP
session. Preview and then install it there:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install-worker-tunnel-key.ps1 `
  -PublicKeyFile .\autocomp-worker.pub `
  -Checkpoint remote-worker-key

powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install-worker-tunnel-key.ps1 `
  -PublicKeyFile .\autocomp-worker.pub `
  -Checkpoint remote-worker-key `
  -Apply
```

If the logged-in KV account belongs to the local Administrators group, run the
second script elevated and add `-AdministratorAccount`. Windows OpenSSH reads
administrator keys from `%ProgramData%\ssh\administrators_authorized_keys`.

The installed key has OpenSSH restrictions that permit port forwarding only to
`127.0.0.1:8765`. It cannot start a shell or remote command, allocate a terminal,
or forward an authentication agent. Keep ordinary RDP/administrator credentials
outside AUTOComp.

### Start the worker on the KV computer

Log into the same interactive Windows account that runs KV STUDIO. Open only the
throwaway project copy, keep the PLC disconnected/offline, and start:

```powershell
cd C:\projects\AUTOComp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start-worker.ps1
```

Leave that PowerShell window open. The worker is intentionally not a Windows
service because Windows UI Automation must run in the same interactive session
as KV STUDIO. A minimized KV STUDIO window is normally sufficient for UIA-only
inventory. Screenshot/VLM fallback requires a visible, unlocked desktop.

`config.local.json` defaults to `safety.apply_enabled: false`. Keep that default
for health, status, ordinary inventory, and all dry-runs. Immediately before a
reviewed full expansion, probe, or rename on the project copy, set it to `true`
and restart the worker. `-Apply` plus a checkpoint is still required on every
such request. Return it to `false` and restart after the mutation batch. Never
enable it while a production/sole project or an online PLC session is open.

## Connect from the controlling computer

Put the same `AUTOCOMP_WORKER_TOKEN` in a local ignored `.env.remote` file. Also
set the tunnel endpoint:

```dotenv
AUTOCOMP_WORKER_ENDPOINT=http://127.0.0.1:8765
AUTOCOMP_WORKER_TOKEN=the-same-random-token-from-the-KV-computer
```

Never paste the token into chat, Git, a URL, or a PowerShell command. Open the
tunnel in one PowerShell window:

```powershell
cd W:\_python\AUTOComp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\connect-worker.ps1 `
  -RemoteHost 192.168.1.50 `
  -RemoteUser thunder `
  -IdentityFile "$env:USERPROFILE\.ssh\autocomp-worker"
```

Keep the tunnel window open. In another PowerShell window, verify the authenticated
worker and inspect its explicit capabilities:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -Health -EnvFile .env.remote

powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -Capabilities -EnvFile .env.remote

powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -Status -EnvFile .env.remote
```

Capture the ordinary read-only UI inventory without overwriting an old report:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -Inventory -EnvFile .env.remote `
  -Output reports\remote-ui.json
```

Capture only currently visible tree nodes without expanding anything:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -InventoryProjectTree -EnvFile .env.remote `
  -Output reports\remote-tree-visible.json
```

A complete inventory temporarily changes expansion state and therefore requires
both an explicit apply switch and checkpoint. The worker restores the original
state before returning:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -InventoryProjectTree -ExpandAll -Apply `
  -Checkpoint 01-remote-full-tree `
  -EnvFile .env.remote `
  -Output reports\remote-tree-full.json
```

## Name-limit probe and rename-one safety gate

The client has no raw JSON/action option. A tree rename requires the exact
integer locator, full expected path, exact current source text, target text, and
named checkpoint. First submit a probe without `-Apply`; this only validates the
request structure and stale-node guards:

```powershell
$path = @("项目", "程序", "每次扫描执行型模块", "旧名称")
$locator = @(0, 3, 1, 7)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\invoke-worker.ps1 `
  -ProbeTreeItemRename `
  -Locator $locator `
  -ExpectedPath $path `
  -ExpectedSource "旧名称" `
  -Target "Reviewed English Name" `
  -Checkpoint 01-rename-probe `
  -EnvFile .env.remote
```

Only after that response identifies the expected node should the probe be
repeated with `-Apply`. An apply probe temporarily enters the candidate name,
verifies what KV STUDIO accepted, and immediately restores and verifies the
original. This is the required first test for English name-length limits.

After a successful probe and manual review, use `-RenameTreeItem` with the same
guard fields. Run it once without `-Apply`, then repeat it with `-Apply` to keep
the new name. The worker refuses stale paths/sources and records the checkpoint,
before/after values, and rollback result. Translation batches must still be
followed by project save, compile/check diagnostics, and mnemonic comparison.

## Troubleshooting

- `running scripts is disabled`: use the shown `powershell.exe -ExecutionPolicy
  Bypass -File ...` form. It changes policy only for that process.
- SSH cannot connect: verify `sshd` is running and `ClientAddress` is the actual
  controller LAN/VPN address. Use RDP to correct the restricted firewall rule.
- Tunnel opens but health returns 401: the local and remote worker tokens differ.
- Status reports no KV STUDIO window: start KV STUDIO in the same logged-in
  account/session as the worker and open the project copy.
- UIA inventory works but screenshots are black: restore the physical/console
  desktop; do not depend on a disconnected or locked RDP display for vision.

## Rollback of remote access setup

Run rollback through RDP so an SSH mistake cannot lock you out. Disable the
`AUTOComp-SSHD-Restricted` firewall rule. Re-enable
`OpenSSH-Server-In-TCP` only if you intentionally accept its former broad source
scope. Remove only the `autocomp-worker-<checkpoint>` line from the applicable
`authorized_keys` file. Stop `sshd` and change its startup type to Manual if no
other workflow uses SSH. Removing the Windows capability is optional and should
not be combined with unrelated cleanup.
