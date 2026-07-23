from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "build-english-mirror.py"
    spec = importlib.util.spec_from_file_location("build_english_mirror", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def test_builds_48_program_utf8_mirror_without_changing_original_slots(tmp_path: Path) -> None:
    records = []
    hierarchy = []
    programs = []
    for index in range(48):
        locator = [4, 0, index]
        name = f"程序{index}"
        path = ["程序", name]
        records.append(
            {
                "locator": locator,
                "original_name": name,
                "current_tree_name": name,
                "english_name": "stale",
                "original_path": path,
                "english_path": ["stale"],
            }
        )
        hierarchy.append(
            {
                "locator": locator,
                "names": {"original": name, "current": name, "english": "stale"},
                "paths": {"original": path, "current": path, "english": ["stale"]},
                "children": [],
            }
        )
        raw_text = (
            f"DEVICE:132\r\n;MODULE:{name}\r\n;/*测金/语音*/\r\n"
            ';DM1300.T="语音"\r\nSMOV "语音" DM1300\r\nENDH\r\n'
        )
        raw_bytes = raw_text.encode("cp936")
        lines = [
            {"number": number, "text": text, "eol": "CRLF"}
            for number, text in enumerate(raw_text.split("\r\n")[:-1], start=1)
        ]
        original = {
            "file": f"{name}.mnm",
            "capture": {"method": "mnemonic_export", "source": f"{name}.mnm"},
            "raw_bytes_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "raw_bytes_size": len(raw_bytes),
            "raw_bytes_base64": base64.b64encode(raw_bytes).decode("ascii"),
            "encoding": "cp936",
            "raw_text": raw_text,
            "newline_styles": ["CRLF"],
            "terminal_newline": True,
            "lines": lines,
            "commands": [
                {"line": 1, "text": "DEVICE:132", "opcode": "DEVICE"},
                {"line": 5, "text": 'SMOV "语音" DM1300', "opcode": "SMOV"},
                {"line": 6, "text": "ENDH", "opcode": "ENDH"},
            ],
            "cjk_occurrences": [],
        }
        programs.append(
            {
                "tree_map": {
                    "locator": locator,
                    "names": {"original": name, "current": name, "english": "stale"},
                    "paths": {"original": path, "current": path, "english": ["stale"]},
                },
                "content": {"original": original, "current": None, "english": None},
            }
        )
    source = {
        "schema_version": 1,
        "artifact_type": "fixture",
        "content_slot": "original",
        "complete": True,
        "tree": {"records": records},
        "tree_hierarchy": hierarchy,
        "programs": programs,
        "missing_program_locators": [],
        "unmatched_exports": [],
    }
    source_path = tmp_path / "source.json"
    translations_path = tmp_path / "translations.json"
    output_path = tmp_path / "mirror.json"
    mnemonic_dir = tmp_path / "english"
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    translations_path.write_text(
        json.dumps({"translations": {"测金": "XRF Assay", "语音": "Voice"}}),
        encoding="utf-8",
    )

    assert (
        SCRIPT.main(
            [
                str(translations_path),
                "--input",
                str(source_path),
                "--output",
                str(output_path),
                "--mnemonic-dir",
                str(mnemonic_dir),
            ]
        )
        == 0
    )

    result = json.loads(output_path.read_text(encoding="utf-8"))
    first = result["programs"][0]
    assert result["english_mirror"]["program_count"] == 48
    assert result["english_mirror"]["remaining_cjk_count"] == 144
    assert len(list(mnemonic_dir.glob("*.mnm"))) == 48
    assert first["content"]["original"] == source["programs"][0]["content"]["original"]
    assert first["tree_map"]["names"]["english"] == "stale"
    assert result["tree"]["records"][0]["english_name"] == "stale"
    assert first["content"]["english"]["encoding"] == "utf-8"
    assert first["content"]["english"]["raw_text"] == (
        "DEVICE:132\r\n;MODULE:程序0\r\n;/*XRF Assay/Voice*/\r\n"
        ';DM1300.T="语音"\r\nSMOV "语音" DM1300\r\nENDH\r\n'
    )
    assert first["content"]["english"]["commands"][1]["text"] == 'SMOV "语音" DM1300'
    assert {item["reason"] for item in result["english_mirror"]["allowed_remaining"]} == {
        "runtime Mandarin voice",
        "module identity pending Global rename",
    }
    assert (mnemonic_dir / "001-4_0_0.mnm").read_bytes() == first["content"]["english"][
        "raw_text"
    ].encode("utf-8")
