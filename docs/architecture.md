# Architecture

## Components

`autocomp.translation` owns translation inventory, risk classification, PLC token
protection, glossary/translation memory, and the OpenAI-compatible local-model
client.

`autocomp.verification` owns read-only project hashes, CJK coverage reports,
normalized mnemonic comparison, compile-diagnostic comparison, and checkpoint
reports.

`autocomp.worker` runs inside the same logged-in interactive Windows session as
KV STUDIO. It reads UI Automation controls and exposes only allowlisted actions.
It is not a Windows Session 0 service and it has no PLC or arbitrary-shell API.

The controller can run on the GPU computer. The UI worker stays on the free
Windows computer, so mouse and keyboard focus on the user's workstation are not
used.

## Action pipeline

1. Extract project-owned text through mnemonic/CSV exports where possible.
2. Supplement it with a read-only UI Automation tree inventory.
3. Deduplicate exact source text and enrich every record with hierarchy and nearby
   ladder context.
4. Protect PLC addresses, constants, model names, and reserved tokens.
5. Ask the local model for structured technical-English translations.
6. Review high-risk records and freeze a translation manifest.
7. Apply one small batch through verified KV STUDIO controls.
8. Save As a named checkpoint and export mnemonic lists again.
9. Compare logic and compile diagnostics before continuing.

## Trust boundaries

- The model proposes text and an allowlisted element identifier; it cannot issue
  arbitrary clicks, key sequences, file writes, network calls, or shell commands.
- The worker validates window ownership, action kind, checkpoint, and target
  identity before any future mutation.
- Proprietary KV project files are treated as opaque. Hashing them is allowed;
  editing them is not.
- Behavior-affecting literals remain blocked until classified and approved.

## Why vision is a fallback

UI Automation and exported text provide stable identities and exact strings.
Vision is reserved for custom-drawn controls that expose no usable accessibility
tree. When needed, the worker will send a cropped screenshot with numbered UIA
bounding boxes; the model returns an element number, not raw executable input.
