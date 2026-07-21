# Universal Windows visual worker

The worker is deliberately only eyes and hands. It enumerates visible Windows
windows and owned popups, captures one exact HWND, and performs a small fixed
set of mouse, wheel, keyboard, and Unicode-text operations. It does not know
KV STUDIO, Schneider, project trees, translation rules, shell commands, files,
or PLC protocols. All reasoning stays in `universal-vision-agent.py` and the
versioned VLM prompt.

## Start it on the dedicated Windows computer

Open the disposable offline project copy, leave the desktop unlocked, and run:

```powershell
cd C:\projects\AUTOComp
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-worker.ps1 `
  -ListenAddress 0.0.0.0 `
  -AllowRemote
```

`-ExecutionPolicy Bypass` applies only to this PowerShell process and avoids
changing machine policy. Run the worker in the same logged-in interactive
session as the target application. The application may be behind another
window because the worker first tries native HWND capture, but it must not be
minimized and the Windows session must remain unlocked.

The ignored `.env` on this machine must contain a random worker token of at
least 32 characters. `install-worker.ps1` creates one when needed. The default
worker exposes only the four generic desktop actions; the old KV-specific
accelerator is optional and disabled unless `-EnableKVStudioAdapter` is passed.

## Controller configuration

On the computer running the VLM controller, keep these values in ignored local
files, never in Git:

```dotenv
# .env.remote
AUTOCOMP_WORKER_ENDPOINT=http://192.168.56.101:8765
AUTOCOMP_WORKER_TOKEN=the-same-random-token-from-the-worker-computer

# .env
AUTOCOMP_LLM_ENDPOINT=http://127.0.0.1:8080/v1
AUTOCOMP_LLM_MODEL=auto
AUTOCOMP_LLM_API_KEY=
```

Direct HTTP is intended only for a trusted isolated LAN or VMware host-only
network. Do not expose port 8765 with router forwarding or a public interface.

Check the exact deployed build and available primitives:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-worker.ps1 `
  -Health -AllowLanHttp -EnvFile .env.remote

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-worker.ps1 `
  -Capabilities -AllowLanHttp -EnvFile .env.remote
```

Both responses include `build_id`, `boot_id`, and `started_at`. The mission
runner refuses an old worker that does not advertise the universal API.

## Run or resume the current translation

```powershell
python .\scripts\universal-vision-agent.py `
  --mission-file .\missions\kvstudio-program-tree-en.json `
  --state-file .\.autocomp\kv-program-tree-vision-state.json `
  --apply
```

Every turn follows the same application-independent loop:

1. enumerate current windows and popups;
2. capture a fresh pinned-window PNG;
3. ask the configured vision model for one strict JSON decision;
4. validate coordinates, operation shape, and approved typed text;
5. durably record intent before input;
6. execute through the dumb worker and verify on a new frame.

Menus, scrolling, dialogs, and confirmation always get a new frame. The only
allowed multi-operation response is an already-visible field replacement:
click, optional Ctrl+A, then exact approved text. Original Chinese, approved
English, future Russian, prompt hashes, worker builds, actions, outcomes, and
all evidence frames remain in resumable mission state.

For another Windows editor, create a new mission/context JSON and tune the same
prompt. Do not add another application-specific worker or controller script.

## Update the remote copy

After a code pull, stop and restart the worker so its `build_id` changes:

```powershell
Ctrl+C
git pull
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-worker.ps1 `
  -ListenAddress 0.0.0.0 `
  -AllowRemote
```

## Fast troubleshooting

- `running scripts is disabled`: use the exact `powershell.exe
  -ExecutionPolicy Bypass -File ...` command above.
- `PIL does not seem to be installed`: run `install-worker.ps1` again; the
  Windows extra includes Pillow.
- HTTP 401: `.env.remote` and the worker `.env` contain different tokens.
- old-worker/build error: pull and restart the worker once.
- black or stale frames: restore the target application and unlock/reconnect
  the interactive Windows/RDP session.
- a request returns 503 after closing a dialog: the input may have succeeded
  while the HWND disappeared; the controller records the error and verifies
  the new window list/frame before deciding what to do next.
