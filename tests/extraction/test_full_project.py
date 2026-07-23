from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from autocomp.extraction.full_project import build_full_project_catalog


def _record(
    locator: list[int],
    name: str,
    category: str,
    path: list[str],
    *,
    original_name: str | None = None,
) -> dict[str, object]:
    original = original_name or name
    return {
        "locator": locator,
        "category": category,
        "original_name": original,
        "current_tree_name": name,
        "english_name": name,
        "russian_name": "",
        "english_status": "unchanged",
        "translation_record_id": None,
        "original_path": [*path[:-1], original],
        "english_path": path,
    }


def test_merges_lossless_mnemonic_content_into_all_language_slots(tmp_path: Path) -> None:
    records = [
        {
            "locator": [4],
            "category": "system_ui",
            "original_name": "程序: Demo",
            "current_tree_name": "程序: Demo",
            "english_name": "Program: Demo",
            "russian_name": "",
            "english_status": "reference",
            "translation_record_id": None,
            "original_path": ["程序: Demo"],
            "english_path": ["Program: Demo"],
        },
        {
            "locator": [4, 0],
            "category": "system_ui",
            "original_name": "模块",
            "current_tree_name": "模块",
            "english_name": "Modules",
            "russian_name": "",
            "english_status": "reference",
            "translation_record_id": None,
            "original_path": ["程序: Demo", "模块"],
            "english_path": ["Program: Demo", "Modules"],
        },
        {
            "locator": [4, 0, 1],
            "category": "program",
            "original_name": "测试程序",
            "current_tree_name": "TestProgram",
            "english_name": "TestProgram",
            "russian_name": "Тест",
            "english_status": "applied",
            "translation_record_id": "record-1",
            "original_path": ["程序: Demo", "模块", "测试程序"],
            "english_path": ["Program: Demo", "Modules", "TestProgram"],
        },
    ]
    catalog = {"schema_version": 1, "summary": {"total_nodes": 3}, "records": records}
    raw = 'Program: TestProgram\r\n/* 中文 */\r\nLD M0\r\nMOV "值" DM0\r\n'.encode("gb18030")
    (tmp_path / "TestProgram.mnm").write_bytes(raw)

    result = build_full_project_catalog(catalog, tmp_path)

    assert result["complete"] is True
    assert result["tree"] == catalog
    assert result["summary"] == {
        "tree_nodes": 3,
        "tree_programs": 1,
        "mnemonic_exports": 1,
        "mapped_programs": 1,
        "missing_programs": 0,
        "unmatched_exports": 0,
        "cjk_occurrences": 2,
    }
    program = result["programs"][0]
    assert program["tree_map"]["names"] == {
        "original": "测试程序",
        "current": "TestProgram",
        "english": "TestProgram",
        "russian": "Тест",
    }
    assert program["tree_map"]["paths"]["current"] == [
        "程序: Demo",
        "模块",
        "TestProgram",
    ]
    assert program["content"]["original"] is None
    assert program["content"]["english"] is None
    assert program["content"]["russian"] is None
    content = program["content"]["current"]
    assert content["encoding"] == "gb18030"
    assert content["raw_bytes_sha256"] == hashlib.sha256(raw).hexdigest()
    assert content["raw_text"].endswith('MOV "值" DM0\r\n')
    assert content["lines"][2] == {"number": 3, "text": "LD M0", "eol": "CRLF"}
    assert base64.b64decode(content["raw_bytes_base64"]) == raw
    assert content["newline_styles"] == ["CRLF"]
    assert content["terminal_newline"] is True
    assert [command["opcode"] for command in content["commands"]] == [
        "Program",
        "LD",
        "MOV",
    ]
    assert content["cjk_occurrences"] == [
        {"line": 2, "column": 4, "text": "中文"},
        {"line": 4, "column": 6, "text": "值"},
    ]


def test_discovers_already_english_program_from_system_children(
    tmp_path: Path,
) -> None:
    records = [
        _record([4], "Programs", "system_ui", ["Programs"], original_name="程序"),
        _record([4, 0], "Modules", "system_ui", ["Programs", "Modules"]),
        _record(
            [4, 0, 0],
            "Main",
            "project_structure",
            ["Programs", "Modules", "Main"],
        ),
        _record(
            [4, 0, 0, 0],
            "Local Labels",
            "system_ui",
            ["Programs", "Modules", "Main", "Local Labels"],
            original_name="局部标号",
        ),
        _record(
            [4, 0, 0, 1],
            "Bookmarks",
            "system_ui",
            ["Programs", "Modules", "Main", "Bookmarks"],
            original_name="书签",
        ),
    ]
    catalog = {"schema_version": 1, "records": records}
    (tmp_path / "Main.mnm").write_text("Program: Main\nLD M0\n", encoding="utf-8")

    result = build_full_project_catalog(catalog, tmp_path)

    assert result["complete"] is True
    assert result["summary"]["tree_programs"] == 1
    assert result["summary"]["mapped_programs"] == 1
    assert result["programs"][0]["tree_map"]["names"]["current"] == "Main"
    root = result["tree_hierarchy"][0]
    main = root["children"][0]["children"][0]
    assert main["locator"] == [4, 0, 0]
    assert main["category"] == "project_structure"
    assert main["status"] == "unchanged"
    assert set(main["names"]) == {"original", "current", "english", "russian"}
    assert set(main["paths"]) == {"original", "current", "english", "russian"}
    assert [child["names"]["original"] for child in main["children"]] == [
        "局部标号",
        "书签",
    ]


def test_ingests_selected_extractor_attempt_by_exact_locator(tmp_path: Path) -> None:
    record = _record([4, 0, 7], "Main", "program", ["Programs", "Modules", "Main"])
    catalog = {
        "schema_version": 1,
        "records": [
            _record([4], "Programs", "system_ui", ["Programs"]),
            _record([4, 0], "Modules", "system_ui", ["Programs", "Modules"]),
            record,
        ],
    }
    text = "00001\t/* 中文 */\r\n00002\tLD M0\r\n"
    raw = text.encode("utf-8")
    text_file = Path("programs") / "008-4_0_7-Main.edit-list-popup.txt"
    (tmp_path / text_file).parent.mkdir()
    (tmp_path / text_file).write_bytes(raw)
    state = {
        "schema_version": 1,
        "programs": {
            "opaque-record-id": {
                "name": "does-not-drive-mapping",
                "locator": [4, 0, 7],
                "status": "complete",
                "selected_attempt": 1,
                "attempts": [
                    {"method": "plain", "text_file": "programs/not-selected.txt"},
                    {
                        "method": "edit-list-popup",
                        "text_file": text_file.as_posix(),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "captured_at": "2026-07-22T00:00:00+00:00",
                    },
                ],
            }
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "WrongName.mnm").write_text("must be ignored", encoding="utf-8")

    result = build_full_project_catalog(catalog, tmp_path)

    assert result["complete"] is True
    assert result["content_source_mode"] == "extractor_state"
    assert result["summary"]["mnemonic_exports"] == 0
    content = result["programs"][0]["content"]["current"]
    assert content["raw_text"] == text
    assert content["raw_bytes_sha256"] == hashlib.sha256(raw).hexdigest()
    assert content["capture"] == {
        "method": "edit-list-popup",
        "source": "extract-kvstudio-full-content",
        "state_file": "state.json",
        "program_record_id": "opaque-record-id",
        "selected_attempt": 1,
        "text_file": text_file.as_posix(),
        "captured_at": "2026-07-22T00:00:00+00:00",
    }
