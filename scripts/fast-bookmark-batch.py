"""Fast deterministic bookmark-comment editing for one calibrated KV STUDIO view."""

from __future__ import annotations

import argparse
import base64
import json
import os
import runpy
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

_SELECTED_COMMENT_RGB = (159, 207, 240)


def _find_unique_comment_band(
    image: Image.Image,
    *,
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> tuple[int, tuple[int, int]]:
    """Return the center and inclusive Y bounds of one long exact-color band."""
    rgb = image.convert("RGB")
    if not (0 <= left < right <= rgb.width and 0 <= top < bottom <= rgb.height):
        raise ValueError("scan bounds are outside the full-window screenshot")
    width = right - left
    minimum_run = max(32, width // 2)
    pixels = rgb.load()
    matching_rows: list[int] = []
    for y in range(top, bottom):
        longest = current = 0
        for x in range(left, right):
            if pixels[x, y] == _SELECTED_COMMENT_RGB:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        if longest >= minimum_run:
            matching_rows.append(y)

    bands: list[tuple[int, int]] = []
    for y in matching_rows:
        if bands and y == bands[-1][1] + 1:
            bands[-1] = (bands[-1][0], y)
        else:
            bands.append((y, y))
    bands = [band for band in bands if band[1] - band[0] + 1 >= 3]
    if len(bands) != 1:
        raise ValueError(f"expected one long selected-comment band, found {len(bands)}")
    band = bands[0]
    return (band[0] + band[1]) // 2, band


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
        source = raw.get("source", raw.get("source_text"))
        target = raw.get("target", raw.get("target_text"))
        if not isinstance(record_id, str) or not record_id or len(record_id) > 128:
            raise ValueError("every item requires a bounded record_id")
        if (
            not isinstance(tree_y, int)
            or isinstance(tree_y, bool)
            or not 0 <= tree_y <= 100_000
        ):
            raise ValueError(f"{record_id}: tree_y must be an integer from 0 to 100000")
        for field_name, value in (("source", source), ("target", target)):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 512
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(
                    f"{record_id}: {field_name} must be 1-512 printable characters"
                )
        if record_id in seen_ids or tree_y in seen_rows:
            raise ValueError("record_id and tree_y must be unique within a batch")
        seen_ids.add(record_id)
        seen_rows.add(tree_y)
        items.append(
            {
                "record_id": record_id,
                "tree_y": tree_y,
                "source": source,
                "target": target,
            }
        )
    return items


def _write_event(log: Any, event: dict[str, Any]) -> None:
    log.write(json.dumps(event, ensure_ascii=False) + "\n")
    log.flush()
    os.fsync(log.fileno())
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply a calibrated no-VLM bookmark-comment batch",
        epilog=(
            'items JSON: [{"record_id":"...","tree_y":320,'
            '"source":"/*报警*/","target":"/*Alarm*/"}]'
        ),
    )
    parser.add_argument("--items-json", required=True)
    parser.add_argument("--tree-x", required=True, type=int)
    parser.add_argument("--editor-x", required=True, type=int)
    parser.add_argument("--commit-x", required=True, type=int)
    parser.add_argument("--commit-y", required=True, type=int)
    parser.add_argument("--scan-left", required=True, type=int)
    parser.add_argument("--scan-right", required=True, type=int)
    parser.add_argument("--scan-top", required=True, type=int)
    parser.add_argument("--scan-bottom", required=True, type=int)
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--window-title-contains", default="KV STUDIO - [")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("fast bookmark batch requires explicit --apply")
    coordinates = (
        args.tree_x,
        args.editor_x,
        args.commit_x,
        args.commit_y,
        args.scan_left,
        args.scan_right,
        args.scan_top,
        args.scan_bottom,
    )
    if any(value < 0 or value > 100_000 for value in coordinates):
        raise SystemExit("all coordinates must be between 0 and 100000")

    project = Path(__file__).resolve().parent.parent
    visual = runpy.run_path(str(Path(__file__).with_name("visual-translate.py")))
    values = {
        **visual["_dotenv"](project / args.worker_env),
        **{name: value for name, value in os.environ.items() if value},
    }
    worker_endpoint = values.get("AUTOCOMP_WORKER_ENDPOINT", "").rstrip("/")
    worker_token = values.get("AUTOCOMP_WORKER_TOKEN", "")
    if not worker_endpoint or not worker_token:
        raise SystemExit(f"worker endpoint/token missing in {args.worker_env}")
    settings = visual["Settings"](
        worker_endpoint=worker_endpoint,
        worker_token=worker_token,
        llm_endpoint="",
        llm_key="",
        llm_model="",
    )
    window = visual["_select_window"](settings, args.window_title_contains)
    perform = visual["_perform"]
    snapshot = visual["_snapshot"]
    items = _items(Path(args.items_json).resolve())
    log_path = project / ".autocomp" / f"fast-bookmark-run-{int(time.time())}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("x", encoding="utf-8") as log:
        for item_index, item in enumerate(items, 1):
            tree_action = {
                "action": "double_click",
                "x": args.tree_x,
                "y": item["tree_y"],
            }
            try:
                tree_result = perform(
                    settings,
                    window,
                    tree_action,
                    f"fast_bookmark_{item_index:03d}_01",
                )
            except Exception as exc:
                tree_result = {
                    "performed": False,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            tree_event = {
                "item_index": item_index,
                "record_id": item["record_id"],
                "source": item["source"],
                "target": item["target"],
                "tree_y": item["tree_y"],
                "operation": "tree_double",
                "checkpoint": f"fast_bookmark_{item_index:03d}_01",
                "performed": tree_result.get("performed"),
                "message": tree_result.get("message"),
                "request_id": tree_result.get("request_id"),
            }
            _write_event(log, tree_event)
            if tree_result.get("performed") is not True:
                print(f"Stopped at {item['record_id']}; see {log_path}", flush=True)
                return 2
            time.sleep(0.45)

            try:
                frame = snapshot(settings, window)
                image = Image.open(BytesIO(base64.b64decode(frame["png_base64"])))
                detected_y, band = _find_unique_comment_band(
                    image,
                    left=args.scan_left,
                    right=args.scan_right,
                    top=args.scan_top,
                    bottom=args.scan_bottom,
                )
                if not args.scan_left <= args.editor_x < args.scan_right:
                    raise ValueError("editor_x must be inside the scan x-range")
                if args.commit_y <= band[1]:
                    raise ValueError("commit click must be below the detected edit band")
            except Exception as exc:
                _write_event(
                    log,
                    {
                        "item_index": item_index,
                        "record_id": item["record_id"],
                        "operation": "detect_editor_row",
                        "performed": False,
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                )
                print(f"Stopped at {item['record_id']}; see {log_path}", flush=True)
                return 2
            _write_event(
                log,
                {
                    "item_index": item_index,
                    "record_id": item["record_id"],
                    "operation": "detect_editor_row",
                    "performed": True,
                    "detected_y": detected_y,
                    "band": list(band),
                    "frame_sha256": frame.get("png_sha256"),
                },
            )

            actions = (
                (
                    "editor_double",
                    {"action": "double_click", "x": args.editor_x, "y": detected_y},
                ),
                ("select_all", {"action": "key_ctrl_a"}),
                ("type_target", {"action": "type_text", "text": item["target"]}),
                ("commit_click", {"action": "click", "x": args.commit_x, "y": args.commit_y}),
            )
            for step, (name, action) in enumerate(actions, 2):
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
                _write_event(log, event)
                if result.get("performed") is not True:
                    print(f"Stopped at {item['record_id']}; see {log_path}", flush=True)
                    return 2
                time.sleep(0.18)
            _write_event(
                log,
                {
                    "item_index": item_index,
                    "record_id": item["record_id"],
                    "operation": "item_applied",
                    "applied": True,
                    "detected_y": detected_y,
                    "frame_sha256": frame.get("png_sha256"),
                },
            )

    print(f"Applied {len(items)} bookmark(s); log: {log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
