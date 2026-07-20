# Contributing

Read `AGENTS.md` before changing the project. Safety and logic-preservation rules
are part of the product contract, not optional development guidance.

## Development setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows,dev]"
```

## Required checks

```powershell
ruff check .
python -m pytest -q --basetemp .test-tmp
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir .build-test
```

Every behavior-affecting change needs a focused test. Never add real customer KV
projects, device comments, screenshots, endpoints, credentials, or translation
manifests as fixtures. Use short synthetic examples.

UI mutation code additionally requires evidence from the KV STUDIO 11.62 pilot,
stable allowlisted selectors, a named checkpoint, and before/after mnemonic and
diagnostic comparisons.
