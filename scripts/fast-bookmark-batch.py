"""Fast no-LLM bookmark translator for the current offline KV STUDIO copy.

The worker resolves the exact tree locator.  The controller only finds the
already-selected comment row in the returned client screenshot, then sends the
four edit inputs as one pinned request.  It deliberately stops on the first
unexpected response so the controller can re-inventory before continuing.
"""

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
_ACTIVE_BORDER_RGB = (183, 243, 147)
_DEFAULT_ITEMS = ".autocomp/pending-bookmarks.json"
_DEFAULT_PROGRESS = ".autocomp/fast-bookmark-progress.json"
_DEFAULT_DEFERRED = ".autocomp/deferred-bookmarks.json"


def _find_unique_comment_band(image: Image.Image) -> tuple[int, tuple[int, int]]:
    """Find the one long selected-comment band in a client-area PNG."""
    rgb = image.convert("RGB")
    pixels = rgb.load()
    minimum_run = max(80, rgb.width // 3)
    matching_rows: list[int] = []
    for y in range(rgb.height):
        matching = 0
        for x in range(rgb.width):
            if pixels[x, y] == _SELECTED_COMMENT_RGB:
                matching += 1
        if matching >= minimum_run:
            matching_rows.append(y)
    bands: list[tuple[int, int]] = []
    for y in matching_rows:
        if bands and y == bands[-1][1] + 1:
            bands[-1] = (bands[-1][0], y)
        else:
            bands.append((y, y))
    bands = [band for band in bands if band[1] - band[0] + 1 >= 3]
    if len(bands) > 1:
        active: list[tuple[int, int]] = []
        border_run = max(80, rgb.width // 3)
        for band in bands:
            top_rows = range(max(0, band[0] - 2), band[0])
            bottom_rows = range(band[1] + 1, min(rgb.height, band[1] + 3))
            has_top = any(
                sum(pixels[x, y] == _ACTIVE_BORDER_RGB for x in range(rgb.width))
                >= border_run
                for y in top_rows
            )
            has_bottom = any(
                sum(pixels[x, y] == _ACTIVE_BORDER_RGB for x in range(rgb.width))
                >= border_run
                for y in bottom_rows
            )
            if has_top and has_bottom:
                active.append(band)
        bands = active
    if len(bands) != 1:
        raise ValueError(f"expected one active selected-comment band, found {len(bands)}")
    band = bands[0]
    return (band[0] + band[1]) // 2, band


def _items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items JSON must contain a non-empty items list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("every batch item must be an object")
        record_id = raw.get("record_id")
        locator = raw.get("locator")
        expected_path = raw.get("expected_path")
        source = raw.get("expected_source")
        target = raw.get("target")
        if not isinstance(record_id, str) or not record_id or record_id in seen:
            raise ValueError("every item requires a unique record_id")
        if (
            not isinstance(locator, list)
            or not locator
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in locator
            )
        ):
            raise ValueError(
                f"{record_id}: locator must be a non-empty list of non-negative integers"
            )
        if not isinstance(expected_path, list) or not expected_path or any(
            not isinstance(value, str) or not value for value in expected_path
        ):
            raise ValueError(f"{record_id}: expected_path must be a text list")
        for name, value in (("expected_source", source), ("target", target)):
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError(f"{record_id}: {name} must be 1-512 characters")
        seen.add(record_id)
        result.append(
            {
                "record_id": record_id,
                "locator": locator,
                "expected_path": expected_path,
                "expected_source": source,
                "target": target,
            }
        )
    return result


def _completed_ids(path: Path, items_path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("items_path") != str(items_path):
        raise ValueError(f"progress file belongs to another items list: {path}")
    completed = payload.get("completed_record_ids")
    if not isinstance(completed, list) or any(not isinstance(value, str) for value in completed):
        raise ValueError(f"invalid progress file: {path}")
    return set(completed)


def _save_completed(path: Path, items_path: Path, completed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "items_path": str(items_path),
                "completed_record_ids": sorted(completed),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _id_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(not isinstance(value, str) for value in payload):
        raise ValueError(f"invalid record-id list: {path}")
    return set(payload)


def _save_id_set(path: Path, values: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(values), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _full_window_point(snapshot: dict[str, Any], client_x: int, client_y: int) -> tuple[int, int]:
    """Convert client-PNG coordinates into worker pinned-window coordinates."""
    window = snapshot["window_bounds"]
    client = snapshot["client_bounds"]
    if not (
        isinstance(window, (list, tuple))
        and isinstance(client, (list, tuple))
        and len(window) == len(client) == 4
    ):
        raise ValueError("visual snapshot has invalid bounds")
    x = client_x + int(client[0]) - int(window[0])
    y = client_y + int(client[1]) - int(window[1])
    if x < 0 or y < 0:
        raise ValueError("client bounds are outside the containing window")
    return x, y


def _edit_operations(
    snapshot: dict[str, Any], band_y: int, band: tuple[int, int], target: str
) -> list[dict[str, Any]]:
    width, height = int(snapshot["width"]), int(snapshot["height"])
    if width < 40 or height < 30:
        raise ValueError("visual snapshot is too small")
    # A point near the middle of a selected ladder comment reliably enters its text cell.
    edit_x = width // 2
    commit_y = band[1] + 16 if band[1] + 16 < height else band[0] - 16
    if not 0 <= commit_y < height:
        raise ValueError("no safe point outside selected comment band")
    edit = _full_window_point(snapshot, edit_x, band_y)
    commit = _full_window_point(snapshot, edit_x, commit_y)
    return [
        {"operation": "double", "x": edit[0], "y": edit[1], "pause_ms": 180},
        {"operation": "key_ctrl_a", "pause_ms": 80},
        {"operation": "type_text", "text": target, "pause_ms": 180},
        {"operation": "click", "x": commit[0], "y": commit[1]},
    ]


def _focused_edit_operations(snapshot: dict[str, Any], target: str) -> list[dict[str, Any]]:
    """Replace text when KV already opened its comment editor during activation."""
    width, height = int(snapshot["width"]), int(snapshot["height"])
    commit = _full_window_point(snapshot, width - 30, height - 30)
    return [
        {"operation": "key_ctrl_a", "pause_ms": 80},
        {"operation": "type_text", "text": target, "pause_ms": 180},
        {"operation": "click", "x": commit[0], "y": commit[1]},
    ]


def _apply_focused_visual_edit(
    worker: Any,
    settings: Any,
    snapshot: dict[str, Any],
    target: str,
    checkpoint: str,
) -> dict[str, Any]:
    """Use KV's proven client-relative input while its comment editor has focus."""
    width, height = int(snapshot["width"]), int(snapshot["height"])
    actions = (
        {"operation": "key_ctrl_a"},
        {"operation": "type_text", "text": target},
        {"operation": "click", "x": width - 30, "y": height - 30},
    )
    result: dict[str, Any] = {}
    for step, action in enumerate(actions, 1):
        result = worker(
            settings,
            {
                "action": "visual_input",
                "checkpoint": f"{checkpoint}_{step}",
                **action,
                "apply": True,
            },
        )
        if result.get("performed") is not True:
            return result
        time.sleep(0.08)
    return result


def _wait_for_editing_frame(worker: Any, settings: Any) -> dict[str, Any] | None:
    for _ in range(10):
        refreshed = worker(settings, {"action": "visual_snapshot"})
        frame = refreshed.get("visual_snapshot")
        if isinstance(frame, dict) and "注释编辑中" in str(frame.get("window_title", "")):
            return frame
        time.sleep(0.2)
    return None


def _write_event(log: Any, event: dict[str, Any]) -> None:
    log.write(json.dumps(event, ensure_ascii=False) + "\n")
    log.flush()
    os.fsync(log.fileno())
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply pending bookmark translations without an LLM"
    )
    parser.add_argument("--items-json", default=_DEFAULT_ITEMS)
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--progress-file", default=_DEFAULT_PROGRESS)
    parser.add_argument("--deferred-file", default=_DEFAULT_DEFERRED)
    parser.add_argument("--window-title-contains", default="KV STUDIO - [")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("fast bookmark batch requires explicit --apply")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive")

    project = Path(__file__).resolve().parent.parent
    visual = runpy.run_path(str(Path(__file__).with_name("visual-translate.py")))
    values = {**visual["_dotenv"](project / args.worker_env), **os.environ}
    endpoint = values.get("AUTOCOMP_WORKER_ENDPOINT", "").rstrip("/")
    if not endpoint:
        raise SystemExit(f"worker endpoint missing in {args.worker_env}")
    settings = visual["Settings"](endpoint, values.get("AUTOCOMP_WORKER_TOKEN", ""), "", "", "")
    visual["_select_window"](settings, args.window_title_contains)
    worker = visual["_worker"]
    path = Path(args.items_json)
    if not path.is_absolute():
        path = project / path
    path = path.resolve()
    progress_path = Path(args.progress_file)
    if not progress_path.is_absolute():
        progress_path = project / progress_path
    completed = _completed_ids(progress_path, path)
    deferred_path = Path(args.deferred_file)
    if not deferred_path.is_absolute():
        deferred_path = project / deferred_path
    deferred = _id_set(deferred_path)
    items = [
        item
        for item in _items(path)
        if item["record_id"] not in completed and item["record_id"] not in deferred
    ]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("Nothing pending; progress already contains every item.", flush=True)
        return 0
    log_path = project / ".autocomp" / f"fast-bookmark-run-{int(time.time())}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("x", encoding="utf-8") as log:
        for index, item in enumerate(items, 1):
            checkpoint = f"fast_bookmark_{index:03d}"
            activate = {
                "action": "activate_tree_item",
                "checkpoint": checkpoint + "_activate",
                "locator": item["locator"],
                "expected_path": [*item["expected_path"], item["expected_source"]],
                "expected_source": item["expected_source"],
                "apply": True,
            }
            try:
                current = worker(settings, {"action": "visual_snapshot"})
                frame = current.get("visual_snapshot")
                already_editing = isinstance(frame, dict) and "注释编辑中" in str(
                    frame.get("window_title", "")
                )
                activation_error: Exception | None = None
                if not already_editing:
                    try:
                        result = worker(settings, activate)
                    except RuntimeError as exc:
                        # KV may finish the double-click but make the old UIA wrapper
                        # throw while the editor window is changing modes.
                        activation_error = exc
                        result = {}
                    if result.get("performed") is True:
                        frame = result.get("visual_snapshot")
                    else:
                        frame = _wait_for_editing_frame(worker, settings)
                editing = isinstance(frame, dict) and "注释编辑中" in str(
                    frame.get("window_title", "")
                )
                if activation_error is not None and not editing:
                    raise activation_error
                try:
                    if not isinstance(frame, dict):
                        raise ValueError("activation did not return a snapshot")
                    if editing:
                        band = (-1, -1)
                    else:
                        image = Image.open(BytesIO(base64.b64decode(frame["png_base64"])))
                        selected_y, band = _find_unique_comment_band(image)
                except (KeyError, TypeError, ValueError):
                    # KV STUDIO occasionally paints the newly opened ladder one beat late.
                    time.sleep(0.25)
                    refreshed = worker(settings, {"action": "visual_snapshot"})
                    frame = refreshed.get("visual_snapshot")
                    if not isinstance(frame, dict):
                        raise RuntimeError("follow-up snapshot was unavailable") from None
                    image = Image.open(BytesIO(base64.b64decode(frame["png_base64"])))
                    selected_y, band = _find_unique_comment_band(image)
                if not editing:
                    opened = worker(
                        settings,
                        {
                            "action": "visual_input",
                            "checkpoint": checkpoint + "_open",
                            "operation": "double_click",
                            "x": int(frame["width"]) // 2,
                            "y": selected_y,
                            "apply": True,
                        },
                    )
                    if opened.get("performed") is not True:
                        raise RuntimeError("selected comment did not open")
                    frame = _wait_for_editing_frame(worker, settings)
                    if frame is None:
                        raise RuntimeError("comment editor did not receive focus")
                sequence = _apply_focused_visual_edit(
                    worker,
                    settings,
                    frame,
                    item["target"],
                    checkpoint + "_edit",
                )
                if sequence.get("performed") is not True:
                    raise RuntimeError(sequence.get("message", "edit sequence was not performed"))
            except Exception as exc:
                if isinstance(exc, ValueError) and "selected-comment band" in str(exc):
                    deferred.add(item["record_id"])
                    _save_id_set(deferred_path, deferred)
                    _write_event(
                        log,
                        {
                            "index": index,
                            "record_id": item["record_id"],
                            "status": "deferred_no_comment_band",
                            "error": str(exc),
                        },
                    )
                    continue
                _write_event(
                    log,
                    {
                        "index": index,
                        "record_id": item["record_id"],
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                print(f"Stopped at {item['record_id']}; log: {log_path}", flush=True)
                return 2
            _write_event(
                log,
                {
                    "index": index,
                    "record_id": item["record_id"],
                    "status": "applied",
                    "locator": item["locator"],
                    "source": item["expected_source"],
                    "target": item["target"],
                    "band": list(band),
                },
            )
            completed.add(item["record_id"])
            _save_completed(progress_path, path, completed)

    print(f"Applied {len(items)} bookmark(s); log: {log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
