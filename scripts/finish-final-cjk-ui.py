"""One-off KV STUDIO residual replacement and global CJK audit."""

from __future__ import annotations

import base64
import runpy
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
helper = runpy.run_path(str(ROOT / "scripts" / "batch-kvstudio-english-import.py"))

PID = 15496
MAIN_HANDLE = 1119380
PAIRS = (
    ("使用", "On"),
)


def click(window: dict, checkpoint: str, x: int, y: int, *, allow_gone: bool = False) -> None:
    helper["input_"](window, checkpoint, "click", x=x, y=y, allow_gone=allow_gone)


def open_search() -> dict:
    current = helper["windows"]()
    for window in current:
        if window["process_id"] == PID and window["title"] == "Search" and window["enabled"]:
            return window
    menu = next(
        (
            window
            for window in current
            if window["process_id"] == PID
            and not window["title"]
            and window["owner_handle"] == MAIN_HANDLE
            and window["enabled"]
            and 300 <= window["bounds"][2] - window["bounds"][0] <= 450
            and 180 <= window["bounds"][3] - window["bounds"][1] <= 700
        ),
        None,
    )
    if menu is None:
        main = helper["wait_window"](
            lambda window: window["handle"] == MAIN_HANDLE and window["enabled"],
            "main",
            10,
        )
        click(main, "finish-cjk-edit-menu", 70, 35)
        menu = helper["wait_window"](
            lambda window: window["process_id"] == PID
            and not window["title"]
            and window["owner_handle"] == MAIN_HANDLE
            and window["enabled"]
            and 300 <= window["bounds"][2] - window["bounds"][0] <= 450
            and 180 <= window["bounds"][3] - window["bounds"][1] <= 700,
            "Edit menu",
            5,
        )
    menu_height = menu["bounds"][3] - menu["bounds"][1]
    click(
        menu,
        "finish-cjk-open-search",
        150,
        263 if menu_height > 400 else 175,
        allow_gone=True,
    )
    return helper["wait_window"](
        lambda window: window["process_id"] == PID
        and window["title"] == "Search"
        and window["enabled"],
        "Search",
        10,
    )


def replace_all(search: dict, index: int, source: str, target: str) -> None:
    payload = {
        "action": "desktop_input_sequence",
        "window_handle": search["handle"],
        "expected_pid": search["process_id"],
        "expected_title": search["title"],
        "checkpoint": f"finish-cjk-{index:02d}",
        "operations": [
            {"operation": "click", "x": 290, "y": 76, "pause_ms": 20},
            {"operation": "key_ctrl_a", "pause_ms": 10},
            {"operation": "type_text", "text": source, "pause_ms": 20},
            {"operation": "click", "x": 290, "y": 106, "pause_ms": 20},
            {"operation": "key_ctrl_a", "pause_ms": 10},
            {"operation": "type_text", "text": target, "pause_ms": 20},
            {"operation": "click", "x": 351, "y": 366, "pause_ms": 0},
        ],
        "apply": True,
    }
    try:
        helper["post"](payload)
    except urllib.error.HTTPError as exc:
        if exc.code != 503:
            raise
    result = helper["wait_window"](
        lambda window: window["process_id"] == PID
        and window["title"] == "KV STUDIO"
        and window["enabled"]
        and window["owner_handle"] == search["handle"],
        "replace result",
        10,
    )
    helper["input_"](
        result,
        f"finish-cjk-result-{index:02d}",
        "key_escape",
        allow_gone=True,
    )


def audit(search: dict) -> None:
    # State before this script: Replace is off, regex is on, entire project and
    # all four text-object categories are selected.
    click(search, "finish-cjk-replace-on", 28, 106)
    click(search, "finish-cjk-regex-off", 419, 211)
    for index, (source, target) in enumerate(PAIRS, 1):
        replace_all(search, index, source, target)
        print(f"replaced {index}/{len(PAIRS)}: {source} -> {target}", flush=True)
    click(search, "finish-cjk-replace-off", 28, 106)
    click(search, "finish-cjk-regex-on", 419, 211)
    helper["input_"](search, "finish-cjk-audit-search-focus", "click", x=290, y=76)
    helper["input_"](search, "finish-cjk-audit-search-select", "key_ctrl_a")
    helper["input_"](search, "finish-cjk-audit-pattern", "type_text", text="[一-龥]")
    click(search, "finish-cjk-audit-list", 430, 366, allow_gone=True)


def save_snapshot() -> None:
    main = helper["wait_window"](
        lambda window: window["handle"] == MAIN_HANDLE,
        "main",
        10,
    )
    response = helper["post"](
        {
            "action": "desktop_snapshot",
            "window_handle": main["handle"],
            "expected_pid": main["process_id"],
            "expected_title": main["title"],
        }
    )
    frame = response["desktop_snapshot"]
    path = ROOT / ".autocomp" / "final-residual-cjk-audit.png"
    path.write_bytes(base64.b64decode(frame["png_base64"]))
    print(path, flush=True)


def main() -> None:
    search = open_search()
    audit(search)
    save_snapshot()


if __name__ == "__main__":
    main()
