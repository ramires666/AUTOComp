"""Smart translation batch: translate only items that are still in Chinese."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

project = Path(__file__).resolve().parent.parent
manifest_path = project / "reports" / "03-approved-ui-rename-manifest.json"
agent_path = project / "scripts" / "universal-visual-agent.py"

WORKER_ENDPOINT = "http://192.168.0.183:8765"
WORKER_TOKEN = "08heL611cn0k1GiiSktrAnx8ko5R18H9tOnHoTaM30U="


def get_current_tree_texts() -> set[str]:
    """Get all current tree item texts from the worker."""
    req = Request(
        f"{WORKER_ENDPOINT}/v1/action",
        data=json.dumps(
            {
                "action": "inventory_project_tree",
                "apply": False,
                "expand_all": False,
                "restore_state": True,
            }
        ).encode(),
        headers={
            "Authorization": f"Bearer {WORKER_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8-sig"))
    inventory = data["project_tree_inventory"]
    texts: set[str] = set()

    def collect(node: dict) -> None:
        texts.add(node["name"])
        for child in node["children"]:
            collect(child)

    for root in inventory["roots"]:
        collect(root)
    return texts


def main() -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest["items"]
    current_texts = get_current_tree_texts()

    # Filter to items whose source text still exists in the tree (still Chinese)
    remaining = [item for item in items if item["expected_source"] in current_texts]
    print(f"Total items in manifest: {len(items)}")
    print(f"Remaining to translate: {len(remaining)}")

    if not remaining:
        print("All items already translated!")
        return 0

    batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else len(remaining)
    batch = remaining[:batch_size]

    for index, item in enumerate(batch, 1):
        source = item["expected_source"]
        target = item["target"]
        print(f"[{index}/{len(batch)}] {source} -> {target}")

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
            print(f"  stderr: {result.stderr[:300]}")
            return 1

        time.sleep(0.5)

    print("Batch complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
