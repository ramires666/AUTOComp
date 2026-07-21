"""Fast deterministic bookmark-comment editing for one calibrated KV STUDIO view."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import time
from pathlib import Path
from typing import Any


def _items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items JSON must be a non-empty list or an object with an items list")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_rows: set[int] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("every batch item must be an object")
        record_id = raw.get("record_id")
        tree_y = raw.get("tree_y")
        target = raw.get("target", raw.get("target_text"))
        if not isinstance(record_id, str) or not record_id or len(record_id) > 128:
            raise ValueError("every item requires a bounded record_id")
        if (
            not isinstance(tree_y, int)
            or isinstance(tree_y, bool)
            or not 0 <= tree_y <= 100_000
        ):
            raise ValueError(f"{record_id}: tree_y must be an integer from 0 to 100000")
        if (
            not isinstance(target, str)
            or not target
            or len(target) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in target)
        ):
            raise ValueError(f"{record_id}: target must be 1-512 printable characters")
        if record_id in seen_ids or tree_y in seen_rows:
            raise ValueError("record_id and tree_y must be unique within a batch")
        seen_ids.add(record_id)
        seen_rows.add(tree_y)
        items.append({"record_id": record_id, "tree_y": tree_y, "target": target})
    return items


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply a calibrated no-VLM bookmark-comment batch",
        epilog=(
            'items JSON: [{"record_id":"...","tree_y":320,'
            '"target":"/*Alarm*/"}]'
        ),
    )
    parser.add_argument("--items-json", required=True)
    parser.add_argument("--tree-x", required=True, type=int)
    parser.add_argument("--editor-x", required=True, type=int)
    parser.add_argument("--editor-y", required=True, type=int)
    parser.add_argument("--commit-x", required=True, type=int)
    parser.add_argument("--commit-y", required=True, type=int)
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--llm-env", default=".env")
    parser.add_argument("--window-title-contains", default="KV STUDIO - [")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("fast bookmark batch requires explicit --apply")
    coordinates = (
        args.tree_x,
        args.editor_x,
        args.editor_y,
        args.commit_x,
        args.commit_y,
    )
    if any(value < 0 or value > 100_000 for value in coordinates):
        raise SystemExit("all coordinates must be between 0 and 100000")

    project = Path(__file__).resolve().parent.parent
    visual = runpy.run_path(str(Path(__file__).with_name("visual-translate.py")))
    settings = visual["_settings"](
        project,
        project / args.worker_env,
        project / args.llm_env,
    )
    window = visual["_select_window"](settings, args.window_title_contains)
    perform = visual["_perform"]
    items = _items(Path(args.items_json).resolve())
    log_path = project / ".autocomp" / f"fast-bookmark-run-{int(time.time())}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("x", encoding="utf-8") as log:
        for item_index, item in enumerate(items, 1):
            actions = (
                ("tree_double", {"action": "double_click", "x": args.tree_x, "y": item["tree_y"]}),
                (
                    "editor_double",
                    {"action": "double_click", "x": args.editor_x, "y": args.editor_y},
                ),
                ("select_all", {"action": "key_ctrl_a"}),
                ("type_target", {"action": "type_text", "text": item["target"]}),
                ("commit_click", {"action": "click", "x": args.commit_x, "y": args.commit_y}),
            )
            for step, (name, action) in enumerate(actions, 1):
                checkpoint = f"fast_bookmark_{item_index:03d}_{step:02d}"
                try:
                    result = perform(settings, window, action, checkpoint)
                except Exception as exc:
                    result = {
                        "performed": False,
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                event = {
                    "item_index": item_index,
                    "record_id": item["record_id"],
                    "tree_y": item["tree_y"],
                    "step": step,
                    "operation": name,
                    "checkpoint": checkpoint,
                    "performed": result.get("performed"),
                    "message": result.get("message"),
                    "request_id": result.get("request_id"),
                }
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                log.flush()
                os.fsync(log.fileno())
                print(json.dumps(event, ensure_ascii=False), flush=True)
                if result.get("performed") is not True:
                    print(f"Stopped at {item['record_id']}; see {log_path}", flush=True)
                    return 2
                time.sleep(0.45 if name == "tree_double" else 0.18)

    print(f"Completed {len(items)} bookmark(s); log: {log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
