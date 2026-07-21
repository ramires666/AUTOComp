"""Fast, resumable rename of KV STUDIO program names/comments.

This is deliberately a one-off controller.  It uses the manifest already
reviewed for this project; no LLM calls and no tree scrolling are involved.
The worker remains generic: activate one exact tree item, then perform normal
desktop mouse/keyboard actions in the visible application.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

_CJK = re.compile(r"[\u3400-\u9fff]")
_DEFAULT_TREE = ".autocomp/post-translation-tree.json"
_DEFAULT_MANIFEST = "reports/02-tree-translation-manifest.json"
_DEFAULT_PROGRESS = ".autocomp/fast-program-progress.json"
_REVIEWED_TARGET_OVERRIDES = {
    "A_52号指令:（10#-21#：测金，石墨，载盘）": (
        "A_52 Command: (10#-21#: XRF Assay, Graphite-Crucible, and Carrier-Tray Handling)"
    ),
    "A_54号指令:（在工控指令）": "A_54 Command: (IPC Command)",
    "MQTT:4G通信模块": "MQTT: 4G Communication Module",
    "夹子气缸老化": "Gripper-Cylinder Aging Test",
}


def _split_identifier_comment(text: str) -> tuple[str, str]:
    """KV displays a program as ``identifier: operator comment``."""
    identifier, separator, comment = text.partition(":")
    return identifier.strip(), comment if separator else ""


def _walk(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        if isinstance(node, dict):
            result.append(node)
            children = node.get("children", [])
            if isinstance(children, list):
                result.extend(_walk(children))
    return result


def _program_items(
    tree_payload: dict[str, Any], manifest_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Join remaining CJK program nodes under [4, 0] to exact decisions."""
    inventory = tree_payload.get("project_tree_inventory", {})
    roots = inventory.get("roots", []) if isinstance(inventory, dict) else []
    decisions = manifest_payload.get("decisions", [])
    if not isinstance(roots, list) or not isinstance(decisions, list):
        raise ValueError("invalid tree inventory or translation manifest")
    translations = {
        item.get("source_text"): item.get("target_text")
        for item in decisions
        if isinstance(item, dict)
        and isinstance(item.get("source_text"), str)
        and isinstance(item.get("target_text"), str)
    }
    selected: list[dict[str, Any]] = []
    for node in _walk(roots):
        locator, source, path = node.get("locator"), node.get("name"), node.get("path")
        if not (
            isinstance(locator, list)
            and len(locator) == 3
            and locator[:2] == [4, 0]
            and isinstance(source, str)
            and _CJK.search(source)
            and isinstance(path, list)
        ):
            continue
        target = _REVIEWED_TARGET_OVERRIDES.get(source, translations.get(source))
        if not target:
            raise ValueError(f"no exact manifest decision for program {source!r}")
        source_identifier, source_comment = _split_identifier_comment(source)
        target_identifier, target_comment = _split_identifier_comment(target)
        selected.append(
            {
                "record_id": f"program-{'-'.join(map(str, locator))}",
                "locator": locator,
                "expected_path": path,
                "source": source,
                "target": target,
                "source_identifier": source_identifier,
                "source_comment": source_comment,
                "target_identifier": target_identifier,
                "target_comment": target_comment,
            }
        )
    return selected


def _point(snapshot: dict[str, Any], x: int, y: int) -> tuple[int, int]:
    window, client = snapshot["window_bounds"], snapshot["client_bounds"]
    return x + int(client[0]) - int(window[0]), y + int(client[1]) - int(window[1])


def _selected_tree_row(snapshot: dict[str, Any]) -> tuple[int, int]:
    """Find the selected blue/white row in the left project-tree pane."""
    from base64 import b64decode
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(b64decode(snapshot["png_base64"]))).convert("RGB")
    limit = min(520, max(80, image.width // 3))
    blue_candidates: list[int] = []
    white_candidates: list[int] = []
    for y in range(80, image.height - 10):
        blue = white = 0
        for x in range(0, limit):
            r, g, b = image.getpixel((x, y))
            blue += b > 150 and b > r * 1.25 and b > g * 1.1
            white += r > 225 and g > 225 and b > 225
        # Ignore full-width scroll/status bars; a selected tree row ends at its text.
        if 40 <= blue < limit - 20:
            blue_candidates.append(y)
        elif 80 <= white < limit - 20 and y >= 100:
            white_candidates.append(y)
    candidates = blue_candidates or white_candidates
    if not candidates:
        raise ValueError("selected program tree row was not visible in activation screenshot")
    # Consecutive highlighted scanlines are one row; last band is normally the active one.
    bands: list[list[int]] = []
    for y in candidates:
        if bands and y <= bands[-1][-1] + 1:
            bands[-1].append(y)
        else:
            bands.append([y])
    band = max(bands, key=len)
    return min(limit - 30, 210), (band[0] + band[-1]) // 2


def _sequence_for_dialog(
    item: dict[str, Any], dialog: dict[str, Any], *, identifier_renames: bool
) -> list[dict[str, Any]]:
    left, top, right, bottom = (int(value) for value in dialog["bounds"])
    width, height = right - left, bottom - top
    if width < 160 or height < 100:
        raise ValueError("program properties dialog bounds are implausible")

    def p(x: float, y: float) -> tuple[int, int]:
        return int(width * x), int(height * y)

    ops: list[dict[str, Any]] = []
    if item["source_identifier"] != item["target_identifier"]:
        if not identifier_renames:
            raise ValueError("identifier rename blocked; pass --allow-identifier-renames")
        x, y = p(0.55, 0.06)
        ops += [
            {"operation": "click", "x": x, "y": y},
            {"operation": "key_ctrl_a"},
            {"operation": "type_text", "text": item["target_identifier"]},
        ]
    if item["target_comment"]:
        x, y = p(0.55, 0.55)
        ops += [
            {"operation": "click", "x": x, "y": y},
            {"operation": "key_ctrl_a"},
            {"operation": "type_text", "text": item["target_comment"]},
        ]
    return ops


def _ok_point(dialog: dict[str, Any]) -> tuple[int, int]:
    left, top, right, bottom = (int(value) for value in dialog["bounds"])
    return int((right - left) * 0.67), int((bottom - top) * 0.88)


def _inventory_name_at_locator(payload: dict[str, Any], locator: list[int]) -> str | None:
    inventory = payload.get("project_tree_inventory")
    if not isinstance(inventory, dict):
        return None
    for node in _walk(inventory.get("roots", [])):
        if node.get("locator") == locator and isinstance(node.get("name"), str):
            return node["name"]
    return None


def _read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("completed", [])) if isinstance(data, dict) else set()


def _save_done(path: Path, done: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"completed": sorted(done)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _resolve(project: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (project / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-identifier-renames", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tree", default=_DEFAULT_TREE)
    parser.add_argument("--manifest", default=_DEFAULT_MANIFEST)
    parser.add_argument("--progress", default=_DEFAULT_PROGRESS)
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--window-title-contains", default="KV STUDIO")
    parser.add_argument("--change-name-dx", type=int, default=80)
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing mutation without --apply")
    project = Path(__file__).resolve().parent.parent
    items = _program_items(
        json.loads(_resolve(project, args.tree).read_text(encoding="utf-8")),
        json.loads(_resolve(project, args.manifest).read_text(encoding="utf-8")),
    )
    done_path = _resolve(project, args.progress)
    done = _read_done(done_path)
    items = [item for item in items if item["record_id"] not in done]
    if args.limit:
        items = items[: args.limit]
    visual = runpy.run_path(str(Path(__file__).with_name("visual-translate.py")))
    values = {**visual["_dotenv"](_resolve(project, args.worker_env)), **os.environ}
    settings = visual["Settings"](
        values["AUTOCOMP_WORKER_ENDPOINT"].rstrip("/"),
        values.get("AUTOCOMP_WORKER_TOKEN", ""),
        "",
        "",
        "",
    )
    worker = visual["_worker"]
    main_window = visual["_select_window"](settings, args.window_title_contains)
    existing_dialogs = [
        window
        for window in visual["_desktop_windows"](settings)
        if str(window.get("title", "")).startswith("程序属")
        and not window.get("minimized")
    ]
    for dialog in existing_dialogs:
        with suppress(RuntimeError):
            worker(
                settings,
                {
                    "action": "desktop_input",
                    "window_handle": dialog["handle"],
                    "expected_pid": dialog["process_id"],
                    "expected_title": dialog["title"],
                    "checkpoint": "fast_program_close_stale_dialog",
                    "operation": "key_escape",
                    "apply": True,
                },
            )
    if existing_dialogs:
        time.sleep(0.25)
    for index, item in enumerate(items, 1):
        checkpoint = f"fast_program_{index:03d}"
        try:
            activated = worker(
                settings,
                {
                    "action": "activate_tree_item",
                    "checkpoint": checkpoint + "_activate",
                    "locator": item["locator"],
                    "expected_path": item["expected_path"],
                    "expected_source": item["source"],
                    "apply": True,
                },
            )
            snapshot = activated.get("visual_snapshot")
        except RuntimeError:
            # The native double-click can succeed while its UIA wrapper is
            # disappearing; worker then reports 503 despite the visible change.
            time.sleep(0.35)
            snapshot = worker(settings, {"action": "visual_snapshot"}).get("visual_snapshot")
        if not isinstance(snapshot, dict):
            raise RuntimeError(f"activation yielded no visible tree row: {item['source']}")
        row_x, row_y = _point(snapshot, *_selected_tree_row(snapshot))
        menu_dy = 57 if row_y + 390 < int(snapshot["height"]) else -333
        print(
            f"[{index}/{len(items)}] open {item['source']!r} "
            f"at row=({row_x},{row_y}), menu_dy={menu_dy}",
            flush=True,
        )
        worker(
            settings,
            {
                "action": "desktop_input_sequence",
                "window_handle": main_window["handle"],
                "expected_pid": main_window["process_id"],
                "expected_title": main_window["title"],
                "checkpoint": checkpoint + "_menu",
                "apply": True,
                "operations": [
                    {"operation": "right", "x": row_x, "y": row_y, "pause_ms": 250},
                    {
                        "operation": "click",
                        "x": row_x + args.change_name_dx,
                        "y": row_y + menu_dy,
                        "pause_ms": 500,
                    },
                ],
            },
        )
        dialogs = [
            w
            for w in visual["_desktop_windows"](settings)
            if str(w.get("title", "")).startswith("程序属") and not w.get("minimized")
        ]
        if len(dialogs) != 1:
            raise RuntimeError(f"expected one Program Properties dialog, got {dialogs}")
        dialog = dialogs[0]
        operations = _sequence_for_dialog(
            item, dialog, identifier_renames=args.allow_identifier_renames
        )
        worker(
            settings,
            {
                "action": "desktop_input_sequence",
                "window_handle": dialog["handle"],
                "expected_pid": dialog["process_id"],
                "expected_title": dialog["title"],
                "checkpoint": checkpoint + "_edit",
                "apply": True,
                "operations": operations,
            },
        )
        ok_x, ok_y = _ok_point(dialog)
        # A successful OK closes its own window before the worker can serialize
        # a response, hence an expected transient 503.
        with suppress(RuntimeError):
            worker(
                settings,
                {
                    "action": "desktop_input",
                    "window_handle": dialog["handle"],
                    "expected_pid": dialog["process_id"],
                    "expected_title": dialog["title"],
                    "checkpoint": checkpoint + "_ok",
                    "operation": "click",
                    "x": ok_x,
                    "y": ok_y,
                    "apply": True,
                },
            )
        time.sleep(0.25)
        dialogs_after = [
            window
            for window in visual["_desktop_windows"](settings)
            if str(window.get("title", "")).startswith("程序属") and not window.get("minimized")
        ]
        if dialogs_after:
            raise RuntimeError("Program Properties dialog remained open after OK")
        current_tree = worker(
            settings,
            {
                "action": "inventory_project_tree",
                "expand_all": False,
                "restore_state": True,
                "apply": False,
            },
        )
        actual = _inventory_name_at_locator(current_tree, item["locator"])
        if actual != item["target"]:
            raise RuntimeError(
                f"tree verification failed: expected {item['target']!r}, got {actual!r}"
            )
        done.add(item["record_id"])
        _save_done(done_path, done)
        print(f"[{index}/{len(items)}] {item['source']} -> {item['target']}", flush=True)
        time.sleep(0.12)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
