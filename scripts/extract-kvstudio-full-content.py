"""Extract every KV STUDIO program as raw text through the universal worker.

This is intentionally an application-specific controller.  The worker remains
generic: it only inventories windows, captures a pinned window, reads the
clipboard, and performs bounded keyboard/mouse sequences.  No PLC logic is
typed or otherwise modified by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROGRAM_CHILD_MARKERS = frozenset({"局部标号", "书签"})
EDIT_LIST_TITLE_PREFIX = "编辑列"
REQUIRED_ACTIONS = frozenset(
    {
        "inventory_project_tree",
        "activate_tree_item",
        "desktop_windows",
        "desktop_snapshot",
        "desktop_input_sequence",
        "desktop_clipboard_text",
    }
)
REQUIRED_INPUT_OPERATIONS = frozenset(
    {
        "click",
        "key_ctrl_home",
        "key_ctrl_shift_end",
        "key_ctrl_c",
        "key_ctrl_down",
        "key_ctrl_end",
        "key_ctrl_up",
        "key_ctrl_a",
        "key_escape",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[name.strip()] = value
    return result


def _request_json(
    url: str,
    *,
    token: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        method = "POST"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request failed: {url}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"non-object JSON response from {url}")
    return value


class WorkerClient:
    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token

    def get(self, path: str, *, timeout: float = 30) -> dict[str, Any]:
        return _request_json(
            f"{self.endpoint}{path}", token=self.token, timeout=timeout
        )

    def action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _request_json(
            f"{self.endpoint}/v1/action",
            token=self.token,
            payload=payload,
        )


def _preflight(client: WorkerClient) -> dict[str, Any]:
    health = client.get("/health")
    capabilities = client.get("/v1/capabilities")
    if health.get("status") != "ok" or capabilities.get("mode") != "offline":
        raise RuntimeError("worker must be healthy and in offline mode")
    actions = set(capabilities.get("actions", []))
    missing_actions = sorted(REQUIRED_ACTIONS - actions)
    if missing_actions:
        raise RuntimeError(f"worker is missing actions: {missing_actions}")
    operations = set(capabilities.get("desktop_input_operations", []))
    missing_operations = sorted(REQUIRED_INPUT_OPERATIONS - operations)
    if missing_operations:
        raise RuntimeError(f"worker is missing input operations: {missing_operations}")
    audit = capabilities.get("post_action_audit", {})
    if not isinstance(audit, dict) or audit.get("configured") is not True:
        raise RuntimeError("worker durable post-action audit is not configured")
    build_id = capabilities.get("build_id") or health.get("build_id")
    if not isinstance(build_id, str) or not build_id:
        raise RuntimeError("worker build_id is missing")
    return {
        "build_id": build_id,
        "boot_id": capabilities.get("boot_id") or health.get("boot_id"),
        "started_at": capabilities.get("started_at") or health.get("started_at"),
        "operation_limits": capabilities.get("operation_limits", {}),
    }


def _walk_nodes(nodes: object):  # type: ignore[no-untyped-def]
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        yield from _walk_nodes(node.get("children", []))


def _program_nodes(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Return nodes that directly own a Local Labels or Bookmarks child."""
    programs: dict[tuple[int, ...], dict[str, Any]] = {}
    for node in _walk_nodes(inventory.get("roots", [])):
        children = node.get("children", [])
        if not isinstance(children, list):
            continue
        child_names = {
            str(child.get("name", "")) for child in children if isinstance(child, dict)
        }
        if not child_names.intersection(PROGRAM_CHILD_MARKERS):
            continue
        locator = tuple(int(part) for part in node.get("locator", []))
        path = node.get("path", [])
        if locator and isinstance(path, list) and path and node.get("name"):
            programs[locator] = {
                "name": str(node["name"]),
                "path": [str(part) for part in path],
                "locator": list(locator),
            }
    return [programs[key] for key in sorted(programs)]


def _identity(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "handle": int(window["handle"]),
        "process_id": int(window["process_id"]),
        "title": str(window["title"]),
    }


def _desktop_windows(client: WorkerClient) -> list[dict[str, Any]]:
    response = client.action({"action": "desktop_windows"})
    windows = response.get("desktop_windows")
    if not isinstance(windows, list):
        raise RuntimeError("worker returned no desktop_windows array")
    return [window for window in windows if isinstance(window, dict)]


def _main_window(
    windows: list[dict[str, Any]], *, process_id: int, title: str
) -> dict[str, Any]:
    exact = [
        window
        for window in windows
        if int(window.get("process_id", 0)) == process_id
        and str(window.get("title", "")) == title
        and not window.get("minimized")
    ]
    if exact:
        return max(exact, key=_window_area)
    same_process = [
        window
        for window in windows
        if int(window.get("process_id", 0)) == process_id
        and not window.get("minimized")
        and int(window.get("owner_handle", 0)) == 0
    ]
    if len(same_process) != 1:
        raise RuntimeError("cannot uniquely identify the main editor window")
    return same_process[0]


def _window_area(window: dict[str, Any]) -> int:
    bounds = window.get("bounds", [0, 0, 0, 0])
    if not isinstance(bounds, list) or len(bounds) != 4:
        return 0
    return max(0, int(bounds[2]) - int(bounds[0])) * max(
        0, int(bounds[3]) - int(bounds[1])
    )


def _snapshot(client: WorkerClient, window: dict[str, Any]) -> dict[str, Any]:
    identity = _identity(window)
    response = client.action(
        {
            "action": "desktop_snapshot",
            "window_handle": identity["handle"],
            "expected_pid": identity["process_id"],
            "expected_title": identity["title"],
        }
    )
    frame = response.get("desktop_snapshot")
    if not isinstance(frame, dict):
        raise RuntimeError("worker returned no desktop_snapshot")
    return frame


def _frame_hash(frame: dict[str, Any]) -> str:
    digest = frame.get("png_sha256")
    if not isinstance(digest, str) or not digest:
        raise RuntimeError("worker desktop snapshot has no PNG hash")
    return digest


def _content_point(
    *, frame: dict[str, Any], coordinate_space: str, x: int, y: int
) -> tuple[int, int]:
    width, height = int(frame["width"]), int(frame["height"])
    if width <= 0 or height <= 0:
        raise ValueError("desktop frame has invalid dimensions")
    if coordinate_space == "normalized":
        if not 0 <= x <= 1000 or not 0 <= y <= 1000:
            raise ValueError("normalized content coordinates must be within 0..1000")
        return round(x * (width - 1) / 1000), round(y * (height - 1) / 1000)
    if not 0 <= x < width or not 0 <= y < height:
        raise ValueError("pixel content coordinates are outside the fresh frame")
    return x, y


def _input_sequence(
    client: WorkerClient,
    *,
    window: dict[str, Any],
    checkpoint: str,
    operations: list[dict[str, Any]],
) -> None:
    identity = _identity(window)
    response = client.action(
        {
            "action": "desktop_input_sequence",
            "window_handle": identity["handle"],
            "expected_pid": identity["process_id"],
            "expected_title": identity["title"],
            "checkpoint": checkpoint,
            "operations": operations,
            "apply": True,
        }
    )
    if response.get("performed") is not True:
        raise RuntimeError(str(response.get("message", "desktop input failed")))


def _clipboard(client: WorkerClient, window: dict[str, Any]) -> dict[str, Any]:
    identity = _identity(window)
    response = client.action(
        {
            "action": "desktop_clipboard_text",
            "window_handle": identity["handle"],
            "expected_pid": identity["process_id"],
            "expected_title": identity["title"],
        }
    )
    clipboard = response.get("desktop_clipboard_text")
    if not isinstance(clipboard, dict) or not isinstance(clipboard.get("text"), str):
        raise RuntimeError("worker returned no Unicode clipboard text")
    return clipboard


def _clipboard_hash(clipboard: dict[str, Any]) -> str:
    text = str(clipboard["text"])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clipboard_hash_best_effort(
    client: WorkerClient, window: dict[str, Any]
) -> str | None:
    try:
        return _clipboard_hash(_clipboard(client, window))
    except Exception:
        return None


def _require_changed_clipboard(
    clipboard: dict[str, Any], *, previous_hash: str | None, method: str
) -> None:
    if previous_hash is not None and _clipboard_hash(clipboard) == previous_hash:
        raise RuntimeError(f"{method} copy left a stale clipboard hash")


def _is_post_action_unavailable(error: Exception) -> bool:
    message = str(error).casefold()
    return (
        "http 503" in message
        or "worker_unavailable" in message
        or "worker unavailable" in message
    )


def _edit_list_popup(
    windows: list[dict[str, Any]],
    *,
    main: dict[str, Any],
    previous_handles: set[int],
) -> dict[str, Any] | None:
    main_handle = int(main["handle"])
    process_id = int(main["process_id"])
    candidates = [
        window
        for window in windows
        if int(window.get("handle", 0)) != main_handle
        and int(window.get("handle", 0)) not in previous_handles
        and int(window.get("process_id", 0)) == process_id
        and str(window.get("title", "")).startswith(EDIT_LIST_TITLE_PREFIX)
        and not window.get("minimized")
        and window.get("enabled", True)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda window: (
            int(bool(window.get("foreground"))),
            int(int(window.get("owner_handle", 0)) == main_handle),
            _window_area(window),
        ),
    )


def _wait_for_edit_list_popup(
    client: WorkerClient,
    *,
    main: dict[str, Any],
    previous_handles: set[int],
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        popup = _edit_list_popup(
            _desktop_windows(client),
            main=main,
            previous_handles=previous_handles,
        )
        if popup is not None:
            return popup
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def _wait_for_window_closed(
    client: WorkerClient, *, handle: int, timeout_seconds: float
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        handles = {int(window.get("handle", 0)) for window in _desktop_windows(client)}
        if handle not in handles:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _activate_program(
    client: WorkerClient, *, program: dict[str, Any], checkpoint: str
) -> None:
    response = client.action(
        {
            "action": "activate_tree_item",
            "checkpoint": checkpoint,
            "locator": program["locator"],
            "expected_path": program["path"],
            "expected_source": program["name"],
            "apply": True,
        }
    )
    if response.get("performed") is not True:
        raise RuntimeError(str(response.get("message", "tree activation failed")))


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _record_id(program: dict[str, Any]) -> str:
    material = json.dumps(
        {"locator": program["locator"], "path": program["path"]},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _file_stem(index: int, program: dict[str, Any]) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", program["name"]).strip("_")[:32]
    return f"{index:03d}-{'_'.join(map(str, program['locator']))}-{slug or 'program'}"


def _save_attempt(
    output_dir: Path,
    *,
    stem: str,
    method: str,
    clipboard: dict[str, Any],
) -> dict[str, Any]:
    return _save_text_attempt(
        output_dir,
        stem=stem,
        method=method,
        text=str(clipboard["text"]),
        worker_sha256=str(clipboard.get("sha256", "")),
    )


def _save_text_attempt(
    output_dir: Path,
    *,
    stem: str,
    method: str,
    text: str,
    worker_sha256: str = "",
) -> dict[str, Any]:
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    relative = Path("programs") / f"{stem}.{method}.txt"
    _atomic_write_bytes(output_dir / relative, encoded)
    return {
        "method": method,
        "text_file": relative.as_posix(),
        "characters": len(text),
        "utf8_bytes": len(encoded),
        "sha256": digest,
        "worker_sha256": worker_sha256,
        "captured_at": _now(),
    }


def _join_block_texts(texts: list[str]) -> str:
    """Join blocks without changing their content or merging adjacent lines."""
    joined = ""
    for text in texts:
        if joined and not joined.endswith(("\r", "\n")):
            joined += "\n"
        joined += text
    return joined


def _save_block(
    output_dir: Path,
    *,
    stem: str,
    block_index: int,
    clipboard: dict[str, Any],
    popup: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    text = str(clipboard["text"])
    encoded = text.encode("utf-8")
    relative = Path("programs") / f"{stem}.blocks" / f"{block_index:05d}.txt"
    _atomic_write_bytes(output_dir / relative, encoded)
    metadata = {
        "index": block_index,
        "text_file": relative.as_posix(),
        "characters": len(text),
        "utf8_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "worker_sha256": clipboard.get("sha256", ""),
        "popup": _identity(popup),
        "captured_at": _now(),
    }
    return metadata, text


def _write_blocks_metadata(
    output_dir: Path,
    *,
    stem: str,
    program: dict[str, Any],
    blocks: list[dict[str, Any]],
    complete: bool,
) -> str:
    relative = Path("programs") / f"{stem}.blocks.json"
    _atomic_write_json(
        output_dir / relative,
        {
            "schema_version": 1,
            "program": {
                "name": program["name"],
                "path": program["path"],
                "locator": program["locator"],
            },
            "complete": complete,
            "block_count": len(blocks),
            "blocks": blocks,
            "updated_at": _now(),
        },
    )
    return relative.as_posix()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "created_at": _now(), "programs": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("resume state has an unsupported schema")
    if not isinstance(value.get("programs"), dict):
        raise RuntimeError("resume state has no programs object")
    return value


def _completed_record_valid(output_dir: Path, record: dict[str, Any]) -> bool:
    if record.get("status") != "complete":
        return False
    selected = record.get("selected_attempt")
    attempts = record.get("attempts", [])
    if not isinstance(selected, int) or not isinstance(attempts, list):
        return False
    if not 0 <= selected < len(attempts) or not isinstance(attempts[selected], dict):
        return False
    attempt = attempts[selected]
    path = output_dir / str(attempt.get("text_file", ""))
    if not path.is_file():
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == attempt.get("sha256")


def _extract_one(
    client: WorkerClient,
    *,
    program: dict[str, Any],
    index: int,
    output_dir: Path,
    checkpoint: str,
    coordinate_space: str,
    content_x: int,
    content_y: int,
    edit_menu_x: int,
    edit_menu_y: int,
    edit_list_x: int,
    edit_list_y: int,
    min_text_chars: int,
    pause_ms: int,
    popup_wait_seconds: float,
    max_blocks: int,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        **program,
        "id": _record_id(program),
        "status": "running",
        "attempts": [],
        "errors": [],
        "warnings": [],
        "started_at": _now(),
    }
    stem = _file_stem(index, program)
    _activate_program(
        client, program=program, checkpoint=f"{checkpoint}-activate-{index:03d}"
    )
    time.sleep(0.25)
    inventory_title = str(record.pop("inventory_title"))
    process_id = int(record.pop("process_id"))
    main = _main_window(
        _desktop_windows(client), process_id=process_id, title=inventory_title
    )
    frame = _snapshot(client, main)
    x, y = _content_point(
        frame=frame,
        coordinate_space=coordinate_space,
        x=content_x,
        y=content_y,
    )
    menu_x, menu_y = _content_point(
        frame=frame,
        coordinate_space=coordinate_space,
        x=edit_menu_x,
        y=edit_menu_y,
    )
    list_x, list_y = _content_point(
        frame=frame,
        coordinate_space=coordinate_space,
        x=edit_list_x,
        y=edit_list_y,
    )
    plain_previous_hash = _clipboard_hash_best_effort(client, main)
    try:
        _input_sequence(
            client,
            window=main,
            checkpoint=f"{checkpoint}-copy-{index:03d}",
            operations=[
                {"operation": "click", "x": x, "y": y, "pause_ms": pause_ms},
                {"operation": "key_ctrl_home", "pause_ms": pause_ms},
                {"operation": "key_ctrl_shift_end", "pause_ms": pause_ms},
                {"operation": "key_ctrl_c", "pause_ms": pause_ms},
            ],
        )
        plain_clipboard = _clipboard(client, main)
        _require_changed_clipboard(
            plain_clipboard,
            previous_hash=plain_previous_hash,
            method="plain",
        )
        attempt = _save_attempt(
            output_dir,
            stem=stem,
            method="plain",
            clipboard=plain_clipboard,
        )
        record["attempts"].append(attempt)
        if attempt["characters"] >= min_text_chars:
            record.update(status="complete", selected_attempt=0, completed_at=_now())
            return record
        record["errors"].append(
            f"plain copy returned only {attempt['characters']} characters"
        )
    except Exception as exc:  # Preserve the per-program failure and try edit-list.
        record["errors"].append(f"plain copy: {exc}")

    try:
        _input_sequence(
            client,
            window=main,
            checkpoint=f"{checkpoint}-anchor-edit-list-{index:03d}",
            operations=[
                {"operation": "key_escape", "pause_ms": pause_ms},
                {"operation": "click", "x": x, "y": y, "pause_ms": pause_ms},
                {"operation": "key_ctrl_home", "pause_ms": pause_ms},
            ],
        )
        time.sleep(pause_ms / 1000)
        anchor_frame = _snapshot(client, main)
        record["fallback_start_frame_sha256"] = _frame_hash(anchor_frame)

        blocks: list[dict[str, Any]] = []
        block_texts: list[str] = []
        blocks_file = ""
        reached_end = False
        for block_index in range(1, max_blocks + 1):
            windows_before = _desktop_windows(client)
            previous_handles = {int(window["handle"]) for window in windows_before}
            open_error: Exception | None = None
            try:
                _input_sequence(
                    client,
                    window=main,
                    checkpoint=(
                        f"{checkpoint}-open-edit-list-{index:03d}-{block_index:05d}"
                    ),
                    operations=[
                        {
                            "operation": "click",
                            "x": menu_x,
                            "y": menu_y,
                            "pause_ms": pause_ms,
                        },
                        {
                            "operation": "click",
                            "x": list_x,
                            "y": list_y,
                            "pause_ms": pause_ms,
                        },
                    ],
                )
            except Exception as exc:
                if not _is_post_action_unavailable(exc):
                    raise
                open_error = exc

            popup = _wait_for_edit_list_popup(
                client,
                main=main,
                previous_handles=previous_handles,
                timeout_seconds=popup_wait_seconds,
            )
            if popup is None:
                detail = f": {open_error}" if open_error is not None else ""
                raise RuntimeError(
                    "Edit-menu clicks did not create a new same-PID popup whose title "
                    f"starts with {EDIT_LIST_TITLE_PREFIX!r}{detail}"
                ) from open_error
            if open_error is not None:
                record["warnings"].append(
                    "Edit-menu open returned post-action worker_unavailable, but the exact "
                    f"new popup appeared: {str(open_error)[:500]}"
                )

            copied = False
            try:
                previous_hash = _clipboard_hash_best_effort(client, popup)
                _input_sequence(
                    client,
                    window=popup,
                    checkpoint=(
                        f"{checkpoint}-copy-edit-list-{index:03d}-{block_index:05d}"
                    ),
                    operations=[
                        {"operation": "key_ctrl_a", "pause_ms": pause_ms},
                        {"operation": "key_ctrl_c", "pause_ms": pause_ms},
                    ],
                )
                clipboard = _clipboard(client, popup)
                _require_changed_clipboard(
                    clipboard,
                    previous_hash=previous_hash,
                    method=f"edit-list block {block_index}",
                )
                if not str(clipboard["text"]).strip():
                    raise RuntimeError(
                        f"edit-list block {block_index} returned empty Unicode text"
                    )
                block, block_text = _save_block(
                    output_dir,
                    stem=stem,
                    block_index=block_index,
                    clipboard=clipboard,
                    popup=popup,
                )
                copied = True
            finally:
                try:
                    _input_sequence(
                        client,
                        window=popup,
                        checkpoint=(
                            f"{checkpoint}-close-edit-list-{index:03d}-{block_index:05d}"
                        ),
                        operations=[
                            {"operation": "key_escape", "pause_ms": pause_ms}
                        ],
                    )
                except Exception as exc:
                    record["fatal_cleanup_error"] = True
                    raise RuntimeError(f"edit-list cleanup failed: {exc}") from exc
                if not _wait_for_window_closed(
                    client,
                    handle=int(popup["handle"]),
                    timeout_seconds=popup_wait_seconds,
                ):
                    record["fatal_cleanup_error"] = True
                    raise RuntimeError("edit-list popup remained open after Escape")

            if not copied:
                raise RuntimeError(f"edit-list block {block_index} was not copied")
            blocks.append(block)
            block_texts.append(block_text)
            blocks_file = _write_blocks_metadata(
                output_dir,
                stem=stem,
                program=program,
                blocks=blocks,
                complete=False,
            )

            main = _main_window(
                _desktop_windows(client),
                process_id=process_id,
                title=inventory_title,
            )
            before = _snapshot(client, main)
            before_hash = _frame_hash(before)
            _input_sequence(
                client,
                window=main,
                checkpoint=(
                    f"{checkpoint}-next-block-{index:03d}-{block_index:05d}"
                ),
                operations=[
                    {"operation": "key_ctrl_down", "pause_ms": pause_ms}
                ],
            )
            time.sleep(pause_ms / 1000)
            after_hash = _frame_hash(_snapshot(client, main))
            block["frame_before_ctrl_down_sha256"] = before_hash
            block["frame_after_ctrl_down_sha256"] = after_hash
            block["end_detected"] = before_hash == after_hash
            blocks_file = _write_blocks_metadata(
                output_dir,
                stem=stem,
                program=program,
                blocks=blocks,
                complete=block["end_detected"],
            )
            if block["end_detected"]:
                reached_end = True
                break

        if not reached_end:
            raise RuntimeError(
                f"edit-list traversal reached the hard cap of {max_blocks} blocks"
            )
        joined = _join_block_texts(block_texts)
        if not joined.strip():
            raise RuntimeError("edit-list traversal produced no Unicode text")
        attempt = _save_text_attempt(
            output_dir,
            stem=stem,
            method="edit-list-blocks",
            text=joined,
        )
        attempt.update(block_count=len(blocks), blocks_file=blocks_file)
        record["attempts"].append(attempt)
        record["blocks"] = blocks
        record.update(
            status="complete",
            selected_attempt=len(record["attempts"]) - 1,
            completed_at=_now(),
        )
    except Exception as exc:
        record["errors"].append(f"edit-list traversal: {exc}")
    if record["status"] != "complete":
        record.update(status="error", completed_at=_now())
    return record


def run(args: argparse.Namespace) -> int:
    project = Path(__file__).resolve().parent.parent
    env_path = Path(args.worker_env)
    if not env_path.is_absolute():
        env_path = project / env_path
    values = {**_dotenv(env_path), **os.environ}
    endpoint = values.get("AUTOCOMP_WORKER_ENDPOINT", "").rstrip("/")
    token = values.get("AUTOCOMP_WORKER_TOKEN", "")
    if not endpoint or not token:
        raise RuntimeError("AUTOCOMP_WORKER_ENDPOINT and AUTOCOMP_WORKER_TOKEN are required")
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = project / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    client = WorkerClient(endpoint, token)
    preflight = _preflight(client)
    inventory_response = client.action(
        {
            "action": "inventory_project_tree",
            "checkpoint": f"{args.checkpoint}-inventory",
            "expand_all": True,
            "restore_state": True,
            "apply": True,
        }
    )
    _atomic_write_json(output_dir / "tree-inventory.raw.json", inventory_response)
    inventory = inventory_response.get("project_tree_inventory")
    if not isinstance(inventory, dict):
        raise RuntimeError("worker returned no project_tree_inventory")
    if (
        inventory.get("complete") is not True
        or inventory.get("restoration_complete") is not True
        or inventory.get("truncated") is True
        or inventory.get("warnings")
    ):
        raise RuntimeError("project-tree inventory is incomplete, unrestored, or warned")
    programs = _program_nodes(inventory)
    if not programs:
        raise RuntimeError("no program nodes were found")

    state_path = output_dir / "state.json"
    state = _load_state(state_path)
    state.update(
        checkpoint=args.checkpoint,
        updated_at=_now(),
        worker=preflight,
        inventory={
            "window_title": inventory.get("window_title"),
            "process_id": inventory.get("process_id"),
            "item_count": inventory.get("item_count"),
            "program_count": len(programs),
            "raw_file": "tree-inventory.raw.json",
        },
        coordinates={
            "space": args.coordinate_space,
            "block_anchor": {"x": args.content_x, "y": args.content_y},
            "edit_menu": {"x": args.edit_menu_x, "y": args.edit_menu_y},
            "edit_list": {"x": args.edit_list_x, "y": args.edit_list_y},
        },
    )
    inventory_title = str(inventory["window_title"])
    process_id = int(inventory["process_id"])
    selected = programs[: args.limit] if args.limit else programs
    failures = 0
    for index, program in enumerate(selected, start=1):
        record_id = _record_id(program)
        previous = state["programs"].get(record_id)
        if isinstance(previous, dict) and _completed_record_valid(output_dir, previous):
            print(f"[{index}/{len(selected)}] resume: {program['name']}", flush=True)
            continue
        active_program = {
            **program,
            "inventory_title": inventory_title,
            "process_id": process_id,
        }
        print(f"[{index}/{len(selected)}] extract: {program['name']}", flush=True)
        try:
            record = _extract_one(
                client,
                program=active_program,
                index=index,
                output_dir=output_dir,
                checkpoint=args.checkpoint,
                coordinate_space=args.coordinate_space,
                content_x=args.content_x,
                content_y=args.content_y,
                edit_menu_x=args.edit_menu_x,
                edit_menu_y=args.edit_menu_y,
                edit_list_x=args.edit_list_x,
                edit_list_y=args.edit_list_y,
                min_text_chars=args.min_text_chars,
                pause_ms=args.pause_ms,
                popup_wait_seconds=args.popup_wait_seconds,
                max_blocks=args.max_blocks,
            )
        except Exception as exc:
            record = {
                **program,
                "id": record_id,
                "status": "error",
                "attempts": [],
                "errors": [str(exc)],
                "completed_at": _now(),
            }
        state["programs"][record_id] = record
        state["updated_at"] = _now()
        _atomic_write_json(state_path, state)
        if record.get("status") != "complete":
            failures += 1
        if record.get("fatal_cleanup_error"):
            print("Stopping: edit-list popup could not be closed safely.", flush=True)
            break
    state["status"] = "complete" if failures == 0 else "completed_with_errors"
    state["updated_at"] = _now()
    _atomic_write_json(state_path, state)
    print(
        json.dumps(
            {
                "programs": len(selected),
                "failures": failures,
                "state": str(state_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if failures == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract all KV STUDIO program content through copy/edit-list UI"
    )
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--output-dir", default=".autocomp/kvstudio-full-content")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument(
        "--coordinate-space", choices=("normalized", "pixels"), default="normalized"
    )
    parser.add_argument(
        "--content-x",
        type=int,
        default=724,
        help="initial empty ladder-cell anchor X (default: normalized live 1400/1936)",
    )
    parser.add_argument(
        "--content-y",
        type=int,
        default=287,
        help="initial empty ladder-cell anchor Y (default: normalized live 300/1048)",
    )
    parser.add_argument(
        "--edit-menu-x",
        type=int,
        default=44,
        help="Edit menu X (default: normalized live 85/1936)",
    )
    parser.add_argument(
        "--edit-menu-y",
        type=int,
        default=40,
        help="Edit menu Y (default: normalized live 42/1048)",
    )
    parser.add_argument(
        "--edit-list-x",
        type=int,
        default=72,
        help="List Edit item X (default: normalized live 140/1936)",
    )
    parser.add_argument(
        "--edit-list-y",
        type=int,
        default=616,
        help="List Edit item Y (default: normalized live 645/1048)",
    )
    parser.add_argument("--min-text-chars", type=int, default=20)
    parser.add_argument("--pause-ms", type=int, default=180)
    parser.add_argument("--popup-wait-seconds", type=float, default=0.6)
    parser.add_argument("--max-blocks", type=int, default=10_000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("explicit --apply is required for UI selection/copy actions")
    if args.min_text_chars < 1 or args.limit < 0 or args.max_blocks < 1:
        raise SystemExit(
            "--min-text-chars/--max-blocks must be positive and --limit cannot be negative"
        )
    if not 0 <= args.pause_ms <= 1000 or not 0 <= args.popup_wait_seconds <= 10:
        raise SystemExit("pause values are outside worker bounds")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
