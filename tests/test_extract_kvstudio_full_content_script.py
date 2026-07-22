from __future__ import annotations

import importlib.util
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

