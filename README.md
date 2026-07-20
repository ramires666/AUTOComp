# AUTOComp

AUTOComp is a local-first automation tool for translating user-authored text in
KV STUDIO projects while proving that PLC ladder logic has not changed.

The initial target is Chinese KV STUDIO 11.62. The workflow first completes and
verifies translation in that version, then opens a copy in US/Global 11.62 for a
second compatibility check and final cleanup.

The repository is currently an integration-ready MVP. Inventory, translation,
and offline verification are implemented. UI mutation remains intentionally
disabled until the control identifiers of the real KV STUDIO installation have
been captured during the pilot.

## Safety model

- Project copies only; never the only source project.
- Dry-run by default.
- No PLC connection, monitoring, transfer, or online editing.
- No direct editing of proprietary KV project binaries.
- Every proposed replacement is stored in a reversible manifest.
- Program names, identifiers, and string literals require review.
- Mnemonic logic is compared before and after every edit batch.
- The local model receives translation text and selected UI crops, never shell access.

See [AGENTS.md](AGENTS.md) for permanent development and orchestration rules.
AUTOComp is distributed under the [proprietary license](LICENSE) in this repository.

## Windows installation

Use Python 3.11 or newer on the free Windows computer. Python 3.14 is supported;
the legacy `py` launcher is not required:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[windows]"
Copy-Item config.example.json config.local.json
Copy-Item .env.example .env
```

If `python` is not available but `python3.14` is, use `python3.14 -m venv .venv`.
The installer script searches for `py`, `python`, `python3`, and `python3.14`
automatically. A full executable path can also be supplied with
`-PythonLauncher`.

For an editable development installation with test and lint tools:

```powershell
.\scripts\install-worker.ps1 -Developer
```

Keep the LLM endpoint, model, API key, and worker token in the local `.env` file,
not in `config.local.json`:

```dotenv
AUTOCOMP_LLM_ENDPOINT=http://127.0.0.1:8080/v1
AUTOCOMP_LLM_MODEL=local-vision-model
AUTOCOMP_LLM_API_KEY=
AUTOCOMP_WORKER_TOKEN=
```

Populate `AUTOCOMP_LLM_API_KEY` only if the local server requires it. Set
`AUTOCOMP_WORKER_TOKEN` to a unique random value of at least 32 characters.
The populated `.env` is ignored by Git; `.env.example` contains no secrets.
Real process environment variables override values loaded from the file.
`scripts\install-worker.ps1` generates a unique worker token when it creates
`.env` for the first time and never overwrites an existing local file.

## Implemented commands

Check configuration without touching KV STUDIO:

```powershell
autocomp doctor --config config.local.json
```

Capture a read-only UI Automation inventory while KV STUDIO is open:

```powershell
autocomp inventory-ui --config config.local.json --output reports\uia.json
```

Run the authenticated worker on loopback. Use an SSH/VPN tunnel from the GPU
computer rather than exposing this plain HTTP endpoint directly to the LAN:

```powershell
autocomp worker-serve --config config.local.json --port 8765
```

Equivalent deployment helpers are provided in `scripts\install-worker.ps1` and
`scripts\start-worker.ps1`.

Hash an untouched project copy:

```powershell
autocomp hash-project D:\KVProjects\6260-copy --output reports\00-hashes.json
```

Scan exported text for remaining Chinese text:

```powershell
autocomp scan-cjk exports --output reports\remaining-cjk.json
```

Extract a translation inventory from a KV mnemonic-list export:

```powershell
autocomp extract-mnemonic exports\00\PartsLife.txt `
  --source-name PartsLife `
  --output reports\PartsLife-inventory.json
```

Compare mnemonic exports. Exit code `0` means normalized logic is identical;
exit code `1` means instructions or operands changed:

```powershell
autocomp compare-mnemonic `
  exports\00\PartsLife.txt `
  exports\01\PartsLife.txt `
  --output reports\01-parts-life-logic.json
```

By default semicolon suffixes are preserved as possible logic. Add
`--semicolon-comments` only after the real KV STUDIO 11.62 export confirms they
are comments in that format.

Create a dry-run translation manifest:

```powershell
autocomp translate examples\inventory.example.json `
  --config config.local.json `
  --glossary examples\glossary.example.json `
  --checkpoint 01-pilot-parts-life `
  --output reports\01-translation-manifest.json `
  --memory-output reports\01-translation-memory.json
```

Output files are created exclusively: AUTOComp refuses to overwrite an existing
report accidentally.

## Translation scope

Project-owned content includes program/module names, tab names, bookmarked blue
ladder headings, unbookmarked grey line comments, script comments, and confirmed
operator-facing text literals. Device comments already translated into Russian
can either be retained during the first pilot or converted to English before the
US-version compatibility test.

Chinese nodes such as `项目`, `单元配置`, `CPU 系统设定`, `局部标号`, and `书签`
are application UI, not project names. They become English only in the US/Global
build.

## Planned checkpoint flow

1. `00_original_cn`: immutable project copy, hashes, diagnostics, mnemonic exports.
2. `01_program_names`: project-owned names translated and checked.
3. `02_bookmarks`: heading comments translated and checked.
4. `03_ladder_comments`: remaining line comments translated and checked.
5. `04_scripts_and_strings`: reviewed script text and safe display strings.
6. `05_full_english_cn_verified`: no remaining user-authored Chinese text.
7. Open a copy in US/Global KV STUDIO 11.62, Save As, compile, and run the same checks.

The detailed first-machine procedure is in
[docs/kvstudio-11.62-pilot.md](docs/kvstudio-11.62-pilot.md).

## Development checks

Development and security guidance is in [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md).

```powershell
ruff check .
python -m pytest -q --basetemp .test-tmp
```
