from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_script() -> ModuleType:
    path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "extract-kvstudio-full-content.py"
    )
    spec = importlib.util.spec_from_file_location("extract_kvstudio_full_content", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def test_join_block_texts_preserves_content_and_only_prevents_line_merging() -> None:
    assert SCRIPT._join_block_texts(["LD M0\r\nOUT M1\r\n", "LDB M2\nOUT M3"]) == (
        "LD M0\r\nOUT M1\r\nLDB M2\nOUT M3"
    )
    assert SCRIPT._join_block_texts(["LD M0", "OUT M1"]) == "LD M0\nOUT M1"


def test_edit_list_popup_requires_new_same_pid_title_prefix() -> None:
    main = {
        "handle": 100,
        "process_id": 7,
        "title": "KV STUDIO",
        "bounds": [0, 0, 1000, 700],
    }
    windows = [
        main,
        {
            "handle": 101,
            "process_id": 7,
            "title": "编辑列表",
            "bounds": [0, 0, 500, 400],
        },
        {
            "handle": 102,
            "process_id": 7,
            "title": "DirectInput",
            "bounds": [0, 0, 600, 500],
        },
        {
            "handle": 103,
            "process_id": 9,
            "title": "编辑列表",
            "bounds": [0, 0, 800, 600],
        },
        {
            "handle": 104,
            "process_id": 7,
            "title": "编辑列表 - current block",
            "bounds": [0, 0, 490, 390],
            "foreground": True,
        },
    ]

    popup = SCRIPT._edit_list_popup(
        windows,
        main=main,
        previous_handles={100, 101},
    )

    assert popup is not None
    assert popup["handle"] == 104


def test_saves_selected_kv_studio_clipboard_format_as_resumable_binary(
    tmp_path: Path,
) -> None:
    raw = b"\x00KV-STUDIO\xff\x10"
    digest = hashlib.sha256(raw).hexdigest()
    clipboard_format = {
        "format_id": 49155,
        "name": "CF_KV_STUDIO_2",
        "data_type": "bytes",
        "data_base64": base64.b64encode(raw).decode("ascii"),
        "byte_length": len(raw),
        "sha256": digest,
        "error": "",
    }

    attempt = SCRIPT._save_kv_studio_format_attempt(
        tmp_path,
        stem="001-4_0_0-Main",
        clipboard_format=clipboard_format,
    )

    binary_path = tmp_path / attempt["binary_file"]
    metadata = json.loads((tmp_path / attempt["metadata_file"]).read_text("utf-8"))
    assert binary_path.read_bytes() == raw
    assert metadata["name"] == "CF_KV_STUDIO_2"
    assert metadata["decoded_sha256"] == digest
    assert "data_base64" not in metadata
    assert SCRIPT._completed_record_valid(
        tmp_path,
        {"status": "complete", "selected_attempt": 0, "attempts": [attempt]},
    )


def test_preflight_marks_clipboard_snapshot_as_optional_fast_path() -> None:
    class _Client:
        snapshot_available = True

        def get(self, path: str) -> dict[str, object]:
            if path == "/health":
                return {"status": "ok", "build_id": "build-1"}
            actions = set(SCRIPT.REQUIRED_ACTIONS)
            if self.snapshot_available:
                actions.add("desktop_clipboard_snapshot")
            return {
                "mode": "offline",
                "actions": sorted(actions),
                "desktop_input_operations": sorted(SCRIPT.REQUIRED_INPUT_OPERATIONS),
                "post_action_audit": {"configured": True},
                "build_id": "build-1",
            }

    client = _Client()
    assert SCRIPT._preflight(client)["desktop_clipboard_snapshot_available"] is True
    client.snapshot_available = False
    assert SCRIPT._preflight(client)["desktop_clipboard_snapshot_available"] is False
