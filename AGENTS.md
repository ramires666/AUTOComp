# AUTOComp Development Rules

## Roles and delegation

- The primary agent is the architect and orchestrator. It owns architecture, integration decisions, safety, code review, test coverage, and final output quality.
- For concrete independent subtasks, use the maximum practical number of available fast, low-cost agents. Keep tasks bounded and avoid overlapping file ownership.
- Sub-agent output is advisory or component-level work until the primary agent reviews, integrates, and verifies it.
- Do not trade correctness, safety, or maintainability for delegation speed.

## Product objective

Build a local-first automation tool that inventories and translates user-owned text in Chinese KV STUDIO 11.62 projects, applies changes through supported exports or the interactive Windows UI, and verifies that PLC program logic remains unchanged.

## Safety requirements

- Work on project copies only. Never overwrite the sole source project.
- Default to dry-run. Mutating commands require an explicit apply mode and a named checkpoint.
- Never connect to, monitor, write to, or transfer data to a PLC. The automation must operate in editor/offline mode.
- The UI worker must allowlist KV STUDIO windows and operations. The model must never receive arbitrary shell execution capability.
- The remote worker binds to loopback by default. On a trusted isolated LAN or VMware host-only network, direct HTTP is allowed with explicit non-loopback opt-in and a bearer token; SSH and extra firewall setup are optional. Do not expose it through router port forwarding or a public interface.
- Every remote mutation requires global apply enablement, an explicit per-request apply flag, a named checkpoint, an exact structured tree locator/path/source precondition, and a durable intent audit record before UI input.
- Rename operations must verify the resulting text and automatically attempt rollback to the exact source text after partial, truncated, normalized, or otherwise failed edits.
- Preserve an audit log and a reversible source-to-target translation manifest.
- Treat program names, identifiers, protocol strings, paths, filenames, and external integration keys as higher-risk than comments.
- Verify after every batch. Ladder instructions, operands, and addresses must remain unchanged unless a future user request explicitly authorizes logic changes.

## Implementation standards

- Prefer deterministic extraction and Windows UI Automation over vision. Use OCR/VLM only as a fallback.
- Keep the inference provider behind an OpenAI-compatible interface so a local vision-language model can be selected by configuration.
- Keep secrets out of source files and logs. Bind remote worker endpoints conservatively and require authentication.
- Use typed Python, small modules, structured logs, explicit error handling, and automated tests.
- Keep Windows-specific imports lazy so inventory, translation, and verification tests run on non-Windows development hosts.
- Do not edit proprietary KV STUDIO project binaries directly unless a separately verified format adapter is introduced later.

## Translation and validation rules

- Translate user-authored Chinese text to technical English with hierarchy and nearby ladder context.
- Domain context is mandatory in every translation request: this PLC controls an automated precious-metal acceptance kiosk with a robotic arm, coarse and fine weighing, an induction furnace for melting gold and other precious metals, and an X-ray fluorescence (XRF) analyzer for composition analysis and valuation. Use concise industrial-automation terminology appropriate to that equipment.
- Interpret station and process terms in that domain: `测金` is XRF assay/testing unless local program context clearly identifies another spectrometer, `石墨盘` is a graphite crucible/tray according to local context, `熔炼`/`融金`/`熔金` are induction/gold melting, and tray-position terms describe robot-arm material handling.
- Maintain a project glossary and translation memory so repeated terms stay consistent.
- Do not translate PLC mnemonics, device addresses, numeric constants, hardware model names, or reserved tokens.
- Flag behavior-affecting string literals for review unless they are positively classified as operator-facing text.
- Produce a remaining-CJK report after every full pass.
- Compare baseline and post-edit compile/check diagnostics and normalized mnemonic exports.
