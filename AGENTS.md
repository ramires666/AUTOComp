# AUTOComp Development Rules

## Roles and delegation

- The primary agent is the architect and orchestrator. It owns architecture, integration decisions, safety, code review, test coverage, and final output quality.
- For concrete independent subtasks, use the maximum practical number of available fast, low-cost agents. Keep tasks bounded and avoid overlapping file ownership.
- Sub-agent output is advisory or component-level work until the primary agent reviews, integrates, and verifies it.
- Do not trade correctness, safety, or maintainability for delegation speed.

## MVP execution priority

- This is an MVP for a mostly one-off translation job. Optimize for finishing the real project quickly and reliably, not for production-grade architecture or hypothetical reuse.
- Prefer the shortest working implementation. Do not add frameworks, layers, generic pipelines, or defensive machinery unless they directly unblock the current translation.
- Use the maximum practical number of fast, low-cost agents for independent bounded work. The primary agent reviews their output, integrates it, and controls the live UI.
- Do not run large or repetitive test suites after every small change. Use syntax/lint checks and a small targeted smoke test proportional to the changed code; run broader validation only at a meaningful batch or final checkpoint.
- Verify translations once per batch/page instead of taking and reviewing a screenshot after every successful click. Stop immediately only on a visible mismatch, modal dialog, repeated action, or failed worker response.
- Keep one primary fast path and one visual fallback. Delete superseded experiments, duplicate scripts, generated caches, and temporary code when they are no longer needed.
- Prefer concrete progress updates and working batches over lengthy planning, status narration, or speculative hardening.

## Product objective

Build a local-first visual Windows automation tool for one-off translation of user-owned PLC editor projects, initially Chinese KV STUDIO 11.62 and later Schneider Electric engineering software. Application-specific reasoning belongs in the controller/VLM, not the remote worker.

- The primary execution path is one application-agnostic VLM mission controller. On every step it receives a fresh screenshot, current window metadata, mission data, and durable history; it chooses the next action, verifies the following frame, and resumes from saved state after interruption.
- Intelligence and task state stay in the controller, which may be deployed beside the worker as one target-machine bundle. The remote worker remains an application-agnostic Windows screenshot/input executor (eyes and hands), not an autonomous planner.
- Do not hard-code KV STUDIO UI structure into the universal visual worker. The same window enumeration, pinned-window screenshot, mouse, wheel, fixed-key, and Unicode-text primitives must work with other Windows PLC editors.
- Normal application changes must require only a new mission/context file and prompt tuning, never a new application-specific controller script. Keep the universal VLM system prompt versioned and record its hash in mission state.

## Safety requirements

- Work on project copies only. Never overwrite the sole source project.
- Default to dry-run. Mutating commands require an explicit apply mode and a named checkpoint.
- Never connect to, monitor, write to, or transfer data to a PLC. The automation must operate in editor/offline mode.
- The UI worker must pin every input to an explicitly selected visible window identity (native handle, PID, and expected title). The model must never receive shell execution or process-launch capability.
- The remote worker binds to loopback by default. On a trusted isolated LAN or VMware host-only network, direct HTTP is allowed with explicit non-loopback opt-in and a bearer token; SSH and extra firewall setup are optional. Do not expose it through router port forwarding or a public interface.
- Every remote mutation requires global apply enablement, an explicit per-request apply flag, a named checkpoint, an exact structured tree locator/path/source precondition, and a durable intent audit record before UI input.
- Rename operations must verify the resulting text and automatically attempt rollback to the exact source text after partial, truncated, normalized, or otherwise failed edits.
- Preserve an audit log and a reversible source-to-target translation manifest.
- Treat program names, identifiers, protocol strings, paths, filenames, and external integration keys as higher-risk than comments.
- Verify after every batch. Ladder instructions, operands, and addresses must remain unchanged unless a future user request explicitly authorizes logic changes.

## Implementation standards

- For GUI execution, visual reasoning is primary; deterministic extraction/UI Automation may accelerate inventory and verification but must never be required for a new editor.
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
