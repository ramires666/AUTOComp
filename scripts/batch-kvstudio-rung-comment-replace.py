"""One-off exact-match replacement of translated KV STUDIO rung comments."""

from __future__ import annotations

import json
import re
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / "scripts" / "batch-kvstudio-english-import.py"))
POST = HELPER["post"]
WINDOWS = HELPER["windows"]
WAIT_WINDOW = HELPER["wait_window"]
INPUT = HELPER["input_"]
PID = HELPER["PID"]
MIRROR = ROOT / "reports" / "12-new-project-english-mirror.json"
INVENTORY = ROOT / ".autocomp" / "global-after-rung-comment-tree.json"
STATE = ROOT / ".autocomp" / "rung-comment-remaining-state.json"
CJK = re.compile(r"[\u3400-\u9fff]")


def visit(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from visit(node.get("children", []))


def translations() -> dict[str, str]:
    mirror = json.loads(MIRROR.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for node in visit(mirror["tree_hierarchy"]):
        source = node["names"]["current"]
        target = node["names"]["english"]
        if CJK.search(source) and target and not CJK.search(target):
            previous = result.setdefault(source, target)
            if previous != target:
                raise RuntimeError(f"conflicting translation for {source!r}")
    return result


def pending_sources(mapping: dict[str, str]) -> list[str]:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))["project_tree_inventory"]
    present = {
        node["name"]
        for node in visit(inventory["roots"])
        if CJK.search(node["name"])
    }
    missing = present - mapping.keys()
    if missing:
        raise RuntimeError(f"unmapped current rung comments: {sorted(missing)!r}")
    return sorted(present)


def search_window() -> dict:
    return WAIT_WINDOW(lambda w: w["title"] == "Search" and w["enabled"], "enabled Search", 10)


def replace_one(source: str, target: str, number: int) -> None:
    search = search_window()
    operations = [
        {"operation": "click", "x": 290, "y": 76},
        {"operation": "key_ctrl_a"},
        {"operation": "type_text", "text": source},
        {"operation": "click", "x": 290, "y": 106},
        {"operation": "key_ctrl_a"},
        {"operation": "type_text", "text": target},
        {"operation": "click", "x": 351, "y": 366},
    ]
    POST(
        {
            "action": "desktop_input_sequence",
            "window_handle": search["handle"],
            "expected_pid": PID,
            "expected_title": search["title"],
            "checkpoint": f"rung-comment-replace-{number:03d}",
            "operations": operations,
            "apply": True,
        }
    )
    result = WAIT_WINDOW(
        lambda w: w["title"] == "KV STUDIO" and w["enabled"]
        and w["owner_handle"] == search["handle"],
        "replacement result",
        10,
    )
    INPUT(result, f"rung-comment-result-{number:03d}", "key_enter", allow_gone=True)
    search_window()


def run() -> None:
    mapping = translations()
    sources = pending_sources(mapping)
    state = (
        json.loads(STATE.read_text(encoding="utf-8"))
        if STATE.exists()
        else {"done": ["/*默认配置*/"]}
    )
    done = set(state.get("done", []))
    for number, source in enumerate(sources, 1):
        if source in done:
            continue
        replace_one(source, mapping[source], number)
        done.add(source)
        STATE.write_text(
            json.dumps({"done": sorted(done)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"replaced {len(done):03d}/{len(sources)} {source} -> {mapping[source]}", flush=True)


if __name__ == "__main__":
    run()
