"""Apply a batch of approved translations using the universal visual agent."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

project = Path(__file__).resolve().parent.parent
manifest_path = project / "reports" / "03-approved-ui-rename-manifest.json"
agent_path = project / "scripts" / "universal-visual-agent.py"

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
items = manifest["items"]

# Start with a small batch to verify the approach works.
batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 5
items = items[:batch_size]

print(f"Applying {len(items)} translations...")

for index, item in enumerate(items, 1):
    source = item["expected_source"]
    target = item["target"]
    print(f"[{index}/{len(items)}] {source} -> {target}")

    task = (
        f"In the pinned window, find the tree item with exact text '{source}'. "
        "Click it once to select it, press key_f2 to enter rename mode, "
        f"type '{target}', then press key_enter to confirm. "
        "If not found after scrolling, mark failed."
    )

    result = subprocess.run(
        [
            sys.executable,
            str(agent_path),
            "--task",
            task,
            "--window-title-contains",
            "KV STUDIO",
            "--max-steps",
            "12",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode == 0:
        print(f"  OK: {source}")
    else:
        print(f"  FAILED: {source}")
        print(f"  stderr: {result.stderr[:200]}")
        break

    time.sleep(1)

print("Batch complete.")
