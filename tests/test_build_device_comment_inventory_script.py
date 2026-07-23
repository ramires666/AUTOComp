from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "build-device-comment-inventory.py"
    spec = importlib.util.spec_from_file_location("build_device_comment_inventory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def test_groups_exact_sources_and_attaches_distinct_module_contexts(tmp_path: Path) -> None:
    comments = tmp_path / "original-comments.csv"
    mnemonics = tmp_path / "mnemonics"
    output = tmp_path / "inventory.json"
    mnemonics.mkdir()
    comments.write_bytes(
        (
            "MR000,,共享注释 ,,\r\n"
            "MR001,,相邻注释,,\r\n"
            "DM000,,共享注释 ,,\r\n"
            "DM001,,末尾注释,,\r\n"
        ).encode("cp936")
    )
    fixtures = [
        ("a.mnm", "模块A", ";<h1/>/*第一段*/", "LD MR0"),
        ("b.mnm", "模块B", ";<h1/>/*第二段*/", "MOV DM0 DM1"),
        ("c.mnm", "模块C", ";<h1/>/*第三段*/", "AND @MR000"),
        ("d.mnm", "模块D", ";<h1/>/*第四段*/", "OR DM000"),
    ]
    for name, module, heading, instruction in fixtures:
        (mnemonics / name).write_bytes(
            (
                f"DEVICE:132\r\n;MODULE:{module}\r\n;MODULE_TYPE:0\r\n"
                f"{heading}\r\n{instruction}\r\nENDH\r\n"
            ).encode("cp936")
        )

    assert (
        SCRIPT.main(
            [
                "--comments",
                str(comments),
                "--mnemonic-dir",
                str(mnemonics),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    shared = result["sources"][0]
    assert result["summary"]["unique_source_count"] == 3
    assert shared["source_text"] == "共享注释 "
    assert [row["address"] for row in shared["rows"]] == ["MR000", "DM000"]
    assert shared["rows"][0]["canonical_address"] == "MR0"
    assert shared["rows"][0]["adjacent_same_prefix"]["next"] == {
        "row_index": 2,
        "address": "MR001",
        "source_text": "相邻注释",
    }
    assert [context["module"] for context in shared["contexts"]] == [
        "模块A",
        "模块C",
        "模块B",
    ]
    assert shared["contexts"][0]["instruction"] == "LD MR0"
    assert shared["contexts"][0]["nearest_heading"] == {
        "line_number": 4,
        "text": ";<h1/>/*第一段*/",
    }
    assert shared["contexts"][1]["indirect"] is True
    assert len(shared["contexts"]) == 3
