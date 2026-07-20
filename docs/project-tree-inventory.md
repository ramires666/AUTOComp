# Project-tree inventory

`inventory-project-tree` captures the logical KV STUDIO project tree, including
offscreen nodes and children that appear only after a branch is expanded.

Run the command from the AUTOComp repository in Windows PowerShell:

```powershell
& .\.venv\Scripts\autocomp.exe inventory-project-tree `
  --config .\config.local.json `
  --env-file .\.env `
  --expand-all `
  --apply `
  --checkpoint "01-project-tree-expanded" `
  --output .\reports\01-project-tree-expanded.json
```

## Safety requirements

Before running it:

1. Open a separately named copy of the KV STUDIO 11.62 project. Do not use the
   only source copy.
2. Keep KV STUDIO offline. Disconnect the computer from the PLC or otherwise
   make PLC transfer, monitoring, and online editing unavailable.
3. Save or discard existing editor changes first. An asterisk in the KV STUDIO
   title indicates unsaved work and makes the checkpoint ambiguous.
4. Close KV STUDIO help pages and browser windows whose titles contain
   `KV STUDIO`. Leave exactly one intended editor window open.
5. Use a new checkpoint name and a new output path. AUTOComp must refuse to
   overwrite an existing report.

`--expand-all` requests a bounded traversal of every discoverable branch in the
native project tree. `--apply` authorizes only the temporary UI state changes
needed to expand those branches; it does not authorize editing project text,
changing ladder logic, connecting to a PLC, or transferring data. The worker
must record the original expansion state and attempt to restore it before the
command exits.

`--checkpoint` identifies this inventory pass in its audit trail. Use a stable,
unique name that also appears in the output filename. `--output` is the JSON
report destination; its parent directory may be created, but an existing file
must not be replaced.

Do not proceed to translation or renaming when the result reports
`complete: false`, `restoration_complete: false`, `truncated: true`, or any
unresolved warning. Capture a fresh inventory after correcting the problem.

## JSON result

The output represents one allowlisted KV STUDIO project tree. The inventory is
nested inside the command audit envelope:

```json
{
  "schema_version": 1,
  "action": "inventory_project_tree",
  "checkpoint": "01-project-tree-expanded",
  "mode": "apply",
  "requested": {"expand_all": true, "restore_state": true},
  "inventory": {
    "window_title": "KV STUDIO - [editor] - [project]",
    "process_id": 1234,
    "automation_id": "ProjectTreeView",
    "item_count": 123,
    "expanded_count": 42,
    "restored_count": 42,
    "restore_requested": true,
    "complete": true,
    "restoration_complete": true,
    "truncated": false,
    "warnings": [],
    "roots": []
  },
  "audit": {
    "operation": "inventory_project_tree",
    "ui_mutation": "expand_collapse_only",
    "project_content_changed": false,
    "plc_operations": "forbidden"
  }
}
```

The counters have the following meaning:

- `item_count` is the number of captured logical tree nodes.
- `expanded_count` is the number of initially collapsed nodes temporarily
  expanded for this inventory.
- `restored_count` is the number returned to their original collapsed state.
- `restore_requested` confirms that restoration was part of the operation.
- `complete` is false when the full discoverable tree could not be captured.
- `restoration_complete` is false when any temporary expansion could not be
  reversed.
- `truncated` is true when depth, item-count, timeout, or UI availability limits
  prevented a complete snapshot.
- `warnings` contains bounded diagnostic messages and must be reviewed even
  when `complete` is true.

Each entry in `roots`, recursively through `children`, has this shape:

```json
{
  "name": "PartsLife",
  "path": ["Program: project", "Scan modules", "PartsLife"],
  "depth": 2,
  "sibling_index": 3,
  "locator": [0, 2, 3],
  "initial_expansion_state": "collapsed",
  "expanded_for_inventory": true,
  "visible": true,
  "truncated": false,
  "children": []
}
```

`path`, rather than `name` alone, is the node identity used for review because
different programs can contain duplicate labels such as `Stop`. A node with an
unknown initial state or `truncated: true` is not safe evidence of a complete
inventory. The report is an inventory artifact only: it does not prove that a
later rename is safe, and high-risk program names and identifiers still require
separate review and post-edit mnemonic/compile verification.
