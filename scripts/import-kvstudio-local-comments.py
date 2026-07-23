"""One-off audited importer for per-program KV STUDIO device-comment CSVs.

Dry-run is the default.  The first live invocation imports only Positioning;
use ``--all`` only after that pilot has been visually checked.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path, PureWindowsPath
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env.remote"
DEFAULT_MANIFEST = ROOT / "comments-export" / "local-english" / "manifest.json"
DEFAULT_REMOTE_DIR = PureWindowsPath(
    r"C:\projects\AUTOComp\comments-export\local-english"
)
STATE = ROOT / ".autocomp" / "local-device-comment-import-state.json"
PID = 15496
MAIN_HANDLE = 1119380

# Verified against .autocomp/global-file-menu.png and
# .autocomp/current-import-comments-dialog.png (window-relative pixels).
FILE_MENU = (22, 35)
READ_DEVICE_COMMENTS = (160, 300)
MODULE_COMBO = (110, 490)
FILENAME_FIELD = (345, 405)
MODULE_LIST_X = 70
MODULE_LIST_FIRST_Y = 35
MODULE_LIST_ROW_HEIGHT = 15
MODULE_LIST_PAGE_DOWN = (195, 427)
MODULE_LIST_SECOND_PAGE_FIRST_INDEX = 19


def env() -> tuple[str, str]:
    values: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return (
        values["AUTOCOMP_WORKER_ENDPOINT"].rstrip("/"),
        values.get("AUTOCOMP_WORKER_TOKEN", ""),
    )


def request_json(
    endpoint: str,
    token: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    method = "GET"
    data = None
    if payload is not None:
        method = "POST"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(
        endpoint + path, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError(f"worker returned non-object response for {path}")
    return result


class Worker:
    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint
        self.token = token

    def get(self, path: str) -> dict[str, Any]:
        return request_json(self.endpoint, self.token, path)

    def post(
        self, payload: dict[str, Any], *, allow_gone: bool = False
    ) -> dict[str, Any]:
        try:
            return request_json(
                self.endpoint, self.token, "/v1/action", payload=payload
            )
        except urllib.error.HTTPError as exc:
            if allow_gone and exc.code == 503:
                return {"performed": True, "transient_closed": True}
            raise

    def windows(self) -> list[dict[str, Any]]:
        result = self.post({"action": "desktop_windows"})
        return list(result["desktop_windows"])

    def wait_window(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        label: str,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for window in self.windows():
                if window["process_id"] == PID and predicate(window):
                    return window
            time.sleep(0.05)
        raise RuntimeError(f"timeout waiting for {label}")

    def input(
        self,
        window: dict[str, Any],
        checkpoint: str,
        operation: str,
        *,
        x: int | None = None,
        y: int | None = None,
        text: str | None = None,
        allow_gone: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": "desktop_input",
            "window_handle": window["handle"],
            "expected_pid": PID,
            "expected_title": window["title"],
            "checkpoint": checkpoint,
            "operation": operation,
            "apply": True,
        }
        if x is not None:
            payload.update(x=x, y=y)
        if text is not None:
            payload["text"] = text
        return self.post(payload, allow_gone=allow_gone)

    def input_sequence(
        self,
        window: dict[str, Any],
        checkpoint: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.post(
            {
                "action": "desktop_input_sequence",
                "window_handle": window["handle"],
                "expected_pid": PID,
                "expected_title": window["title"],
                "checkpoint": checkpoint,
                "operations": operations,
                "apply": True,
            }
        )

    def clipboard_text(self, window: dict[str, Any]) -> str:
        result = self.post(
            {
                "action": "desktop_clipboard_text",
                "window_handle": window["handle"],
                "expected_pid": PID,
                "expected_title": window["title"],
            }
        )
        payload = result.get("desktop_clipboard_text") or {}
        return str(payload.get("text", ""))


def dimensions(window: dict[str, Any]) -> tuple[int, int]:
    left, top, right, bottom = window["bounds"]
    return right - left, bottom - top


def load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    modules = payload["modules"]
    if payload.get("module_count") != 35 or len(modules) != 35:
        raise ValueError("manifest must contain exactly 35 module CSVs")
    seen: set[int] = set()
    for module in modules:
        index = int(module["module_index"])
        if not 1 <= index <= 47 or index in seen:
            raise ValueError(f"invalid or duplicate module index: {index}")
        seen.add(index)
        csv_path = path.parent / str(module["filename"])
        rows = list(
            csv.reader(
                io.StringIO(csv_path.read_bytes().decode("cp936"), newline="")
            )
        )
        if len(rows) != int(module["row_count"]):
            raise ValueError(f"row-count mismatch: {csv_path.name}")
        if any(len(row) != 5 for row in rows):
            raise ValueError(f"non-5-column CSV: {csv_path.name}")
    return list(modules)


def main_window(worker: Worker) -> dict[str, Any]:
    return worker.wait_window(
        lambda window: (
            window["handle"] == MAIN_HANDLE
            and window["enabled"]
            and window["title"].startswith("KV STUDIO")
        ),
        "enabled main KV STUDIO window",
    )


def import_module(
    worker: Worker,
    module: dict[str, Any],
    remote_dir: PureWindowsPath,
) -> None:
    index = int(module["module_index"])
    program_name = str(module["program_name"])
    expected_count = int(module["row_count"])
    remote_path = str(remote_dir / str(module["filename"]))
    tag = f"local-comments-{index:03d}"

    main = main_window(worker)
    worker.input(main, tag + "-file", "click", x=FILE_MENU[0], y=FILE_MENU[1])
    menu = worker.wait_window(
        lambda window: (
            not window["title"]
            and window["owner_handle"] == MAIN_HANDLE
            and 300 <= dimensions(window)[0] <= 350
            and 550 <= dimensions(window)[1] <= 600
        ),
        "File menu",
    )
    worker.input(
        menu,
        tag + "-read-device-comments",
        "click",
        x=READ_DEVICE_COMMENTS[0],
        y=READ_DEVICE_COMMENTS[1],
        allow_gone=True,
    )
    dialog = worker.wait_window(
        lambda window: (
            window["enabled"]
            and window["class_name"] == "#32770"
            and window["title"] in {"Open", "Открыть"}
            and 560 <= dimensions(window)[0] <= 630
            and 490 <= dimensions(window)[1] <= 540
        ),
        "device-comment Open dialog",
    )

    # The native module selector is a non-editable ComboBox, so clipboard paste
    # cannot select it.  Reset its list to the first item, then click the exact
    # row.  The popup shows indices 0..29 on the first page and 19..47 after one
    # page-down click; both layouts were measured in this exact Open dialog.
    if index <= 29:
        row_y = MODULE_LIST_FIRST_Y + index * MODULE_LIST_ROW_HEIGHT
        select_operations = [
            {
                "operation": "click",
                "x": MODULE_COMBO[0],
                "y": MODULE_COMBO[1],
                "pause_ms": 50,
            },
            {"operation": "key_ctrl_home", "pause_ms": 50},
            {"operation": "click", "x": MODULE_LIST_X, "y": row_y},
        ]
    else:
        row_y = MODULE_LIST_FIRST_Y + (
            index - MODULE_LIST_SECOND_PAGE_FIRST_INDEX
        ) * MODULE_LIST_ROW_HEIGHT
        select_operations = [
            {
                "operation": "click",
                "x": MODULE_COMBO[0],
                "y": MODULE_COMBO[1],
                "pause_ms": 50,
            },
            {"operation": "key_ctrl_home", "pause_ms": 50},
            {
                "operation": "click",
                "x": MODULE_LIST_PAGE_DOWN[0],
                "y": MODULE_LIST_PAGE_DOWN[1],
                "pause_ms": 50,
            },
            {"operation": "click", "x": MODULE_LIST_X, "y": row_y},
        ]
    worker.input_sequence(dialog, tag + "-select-module", select_operations)

    worker.input(
        dialog,
        tag + "-filename-refocus",
        "click",
        x=FILENAME_FIELD[0],
        y=FILENAME_FIELD[1],
    )
    worker.input(dialog, tag + "-filename-reselect", "key_ctrl_a")
    worker.input(dialog, tag + "-filename", "type_text", text=remote_path)
    worker.input(dialog, tag + "-open", "key_enter", allow_gone=True)

    result = worker.wait_window(
        lambda window: (
            window["enabled"]
            and window["title"] == "KV STUDIO"
            and window["class_name"] == "#32770"
            and dimensions(window)[0] < 450
            and dimensions(window)[1] < 250
        ),
        "KV STUDIO import result",
        timeout=10.0,
    )
    worker.input(result, tag + "-copy-result", "key_ctrl_c")
    result_text = worker.clipboard_text(result)
    expected = re.compile(
        rf"(?<!\d){expected_count}\s+device comments are read(?!\w)",
        re.IGNORECASE,
    )
    if not expected.search(result_text):
        # Leave the unexpected modal open for inspection; never acknowledge a
        # result whose imported count is not exactly the CSV row count.
        raise RuntimeError(
            f"unexpected import result for {program_name!r}; "
            f"expected {expected_count}, clipboard={result_text!r}"
        )
    worker.input(result, tag + "-ack-result", "key_enter", allow_gone=True)
    main_window(worker)


def write_state(done: list[int]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps({"imported_module_indices": sorted(done)}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--remote-dir", default=str(DEFAULT_REMOTE_DIR))
    parser.add_argument("--pilot", default="Positioning")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    modules = load_manifest(args.manifest.resolve())
    if args.all:
        selected = modules
    else:
        selected = [
            module for module in modules if module["program_name"] == args.pilot
        ]
        if len(selected) != 1:
            raise ValueError(f"pilot program must match exactly once: {args.pilot!r}")

    if not args.apply:
        for module in selected:
            print(
                f"DRY-RUN module {module['module_index']:02d} "
                f"{module['program_name']}: {module['row_count']} rows "
                f"from {module['filename']}"
            )
        print("No GUI actions performed; add --apply after review.")
        return 0

    endpoint, token = env()
    worker = Worker(endpoint, token)
    health = worker.get("/health")
    capabilities = worker.get("/v1/capabilities")
    if health.get("status") != "ok" or capabilities.get("mode") != "offline":
        raise RuntimeError("worker must be healthy and in offline mode")
    required = {
        "click",
        "type_text",
        "key_enter",
        "key_escape",
        "key_ctrl_a",
        "key_ctrl_c",
    }
    available = set(capabilities.get("desktop_input_operations", []))
    if not required <= available:
        raise RuntimeError(
            f"worker lacks required generic input operations: {sorted(required - available)}"
        )

    state = (
        json.loads(STATE.read_text(encoding="utf-8"))
        if STATE.exists()
        else {"imported_module_indices": []}
    )
    done = [int(value) for value in state["imported_module_indices"]]
    for module in selected:
        index = int(module["module_index"])
        if index in done:
            print(f"skip already imported module {index:02d}", flush=True)
            continue
        import_module(worker, module, PureWindowsPath(args.remote_dir))
        done.append(index)
        write_state(done)
        print(
            f"imported module {index:02d} {module['program_name']}: "
            f"{module['row_count']} comments",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
