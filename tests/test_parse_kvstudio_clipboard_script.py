from __future__ import annotations

import base64
import importlib.util
import struct
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "parse-kvstudio-clipboard.py"
    )
    spec = importlib.util.spec_from_file_location("parse_kvstudio_clipboard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def _record(subtype: int, index: int, text: str, *, flags: int = 0) -> bytes:
    encoded = text.encode("cp936") + b"\0"
    fields = struct.pack("<II", index, flags) if subtype == 0x08 else struct.pack("<I", index)
    size = 4 + len(fields) + len(encoded)
    return struct.pack("<I", size) + fields + encoded


def _group(subtype: int, *records: bytes) -> bytes:
    payload = b"".join(records)
    return struct.pack("<HII32s", subtype, len(payload), len(records), b"\0" * 32) + payload


def _script_record(index: int, raw_text_region: bytes) -> bytes:
    body = struct.pack("<I", index) + b"\0" * 64 + raw_text_region
    return struct.pack("<I", 4 + len(body)) + body


def test_parses_confirmed_sections_groups_records_and_cp936_offsets() -> None:
    groups = b"".join(
        (
            _group(0x08, _record(0x08, 7, "熔金", flags=3)),
            _group(0x81, _record(0x81, 9, "XRF测金")),
        )
    )
    header = bytes((index * 17) % 256 for index in range(0x3B6))
    payload = (
        header
        + b"MODULE\0\0"
        + struct.pack("<II", 2, 16 + len(groups))
        + groups
    )

    result = SCRIPT.parse_payload(payload)

    section = result["sections"][0]
    first, second = section["groups"]
    assert result["global_header"]["size"] == 0x3B6
    assert base64.b64decode(result["global_header"]["base64"]) == header
    assert section["offset"] == 0x3B6
    assert section["tag"] == "MODULE"
    assert section["version"] == 2
    assert first["subtype"] == 0x08
    assert first["records"][0]["index"] == 7
    assert first["records"][0]["flags"] == 3
    assert first["records"][0]["text"] == "熔金"
    assert second["subtype_hex"] == "0x81"
    assert second["records"][0]["text"] == "XRF测金"
    assert base64.b64decode(second["records"][0]["raw_text_base64"]) == "XRF测金".encode(
        "cp936"
    )
    assert first["records"][0]["text_offset"] == first["records"][0]["offset"] + 12
    assert second["records"][0]["text_offset"] == second["records"][0]["offset"] + 8
    with pytest.raises(SCRIPT.ParseError, match="exceeds payload boundary"):
        SCRIPT.parse_payload(payload[:-1])


def test_decodes_subtype_09_kv_script_from_offset_72_losslessly() -> None:
    script = "'测金位\r\nIF MR100 THEN\r\nEND IF\0"
    raw_text_region = script.encode("cp936") + b"\0"
    groups = _group(0x09, _script_record(23, raw_text_region))
    payload = b"header" + b"MODULE\0\0" + struct.pack("<II", 6, 16 + len(groups)) + groups

    record = SCRIPT.parse_payload(payload)["sections"][0]["groups"][0]["records"][0]

    assert record["index"] == 23
    assert record["text"] == script
    assert record["text_offset"] == record["offset"] + 72
    assert base64.b64decode(record["reserved_base64"]) == b"\0" * 64
    assert base64.b64decode(record["raw_text_base64"]) == script.encode("cp936")
    assert base64.b64decode(record["raw_text_region_base64"]) == raw_text_region
    assert base64.b64decode(record["raw_record_base64"])[72:] == raw_text_region
