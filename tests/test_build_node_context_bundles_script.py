from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "build-node-context-bundles.py"
    spec = importlib.util.spec_from_file_location("build_node_context_bundles", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def test_builds_48_complete_program_bundles_with_only_referenced_comments(
    tmp_path: Path,
) -> None:
    programs = []
    for index in range(48):
        source_path = ["程序", "模块", f"程序{index}"]
        lines = [
            {"number": 1, "text": "DEVICE:132", "eol": "CRLF"},
            {"number": 2, "text": f";MODULE:程序{index}", "eol": "CRLF"},
            {"number": 3, "text": ";<h1/>/*测试段*/", "eol": "CRLF"},
            {
                "number": 4,
                "text": "LD MR0" if index == 0 else "LD CR2002",
                "eol": "CRLF",
            },
            {"number": 5, "text": 'SMOV "语音" DM0', "eol": "CRLF"},
            {"number": 6, "text": "ENDH", "eol": "CRLF"},
        ]
        programs.append(
            {
                "tree_map": {
                    "locator": [4, 0, index],
                    "names": {"original": f"程序{index}", "english": f"Program {index}"},
                    "paths": {
                        "original": source_path,
                        "english": ["Programs", "Modules", f"Program {index}"],
                    },
                },
                "content": {"original": {"file": f"程序{index}.mnm", "lines": lines}},
            }
        )
    project = {"programs": programs}
    mnemonic_inventory = [
        {
            "record_id": "unit-1",
            "source_text": "测试段",
            "kind": "script_comment",
            "hierarchy": ["程序", "模块", "程序0"],
            "location": "程序0.mnm:3",
        }
    ]
    mnemonic_translations = {"测试段": "Test Section"}
    device_inventory = {
        "sources": [
            {
                "source_text": "复位标志",
                "rows": [
                    {
                        "row_index": 1,
                        "address": "MR000",
                        "canonical_address": "MR0",
                    }
                ],
            },
            {
                "source_text": "未引用",
                "rows": [
                    {
                        "row_index": 2,
                        "address": "MR001",
                        "canonical_address": "MR1",
                    }
                ],
            },
        ]
    }
    device_translations = tmp_path / "device-translations.json"
    device_translations.write_text(
        json.dumps({"translations": {"复位标志": "Reset Flag"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = SCRIPT.build_bundles(
        project,
        mnemonic_inventory,
        mnemonic_translations,
        device_inventory,
        device_translation_path=device_translations,
    )

    assert result["summary"]["program_count"] == 48
    assert result["summary"]["original_line_count"] == 288
    assert result["summary"]["english_line_count"] == 288
    first = result["bundles"][0]
    second = result["bundles"][1]
    assert first["tree"]["source_path"] == ["程序", "模块", "程序0"]
    assert first["tree"]["english_path"] == ["Programs", "Modules", "Program 0"]
    assert first["mnemonic"]["original_lines"][2]["text"] == ";<h1/>/*测试段*/"
    assert first["mnemonic"]["english_lines"][2]["text"] == ";<h1/>/*Test Section*/"
    assert first["mnemonic"]["english_lines"][4]["text"] == 'SMOV "语音" DM0'
    assert first["mnemonic"]["translation_units"][0]["record_id"] == "unit-1"
    assert first["device_comments"] == [
        {
            "address": "MR000",
            "canonical_address": "MR0",
            "source_text": "复位标志",
            "english": "Reset Flag",
            "occurrence_count": 1,
            "contexts": [
                {
                    "line_number": 4,
                    "instruction": "LD MR0",
                    "matched_token": "MR0",
                    "indirect": False,
                    "suffix": "",
                    "nearest_heading": {
                        "line_number": 3,
                        "text": ";<h1/>/*测试段*/",
                    },
                }
            ],
        }
    ]
    assert second["device_comments"] == []
