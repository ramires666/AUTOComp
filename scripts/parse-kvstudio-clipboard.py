"""Parse captured CF_KV_STUDIO_2 binary payloads into lossless JSON metadata."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

_SECTION_HEADER = struct.Struct("<8sII")
_GROUP_HEADER = struct.Struct("<HII32s")
_U32 = struct.Struct("<I")
_TEXT_SUBTYPES = frozenset({0x81, 0x85, 0xA0, 0xA1, 0xA5, 0xA9, 0xAA})
_SECTION_TAGS = {
    b"MODULE\0\0": "MODULE",
    b"COMMENT\0": "COMMENT",
    b"LABEL\0\0\0": "LABEL",
}


class ParseError(ValueError):
    """The payload violates a confirmed CF_KV_STUDIO_2 boundary or field rule."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ParseError(message)


def _u32(data: bytes, offset: int, *, end: int, field: str) -> int:
    _require(offset + _U32.size <= end, f"{field} exceeds its parent boundary")
    return _U32.unpack_from(data, offset)[0]


def _decode_text(raw: bytes, *, offset: int) -> str:
    try:
        return raw.decode("cp936")
    except UnicodeDecodeError as exc:
        raise ParseError(f"invalid CP936 text at offset {offset}: {exc}") from None


def _parse_record(
    data: bytes,
    *,
    offset: int,
    group_end: int,
    subtype: int,
) -> tuple[dict[str, Any], int]:
    size = _u32(data, offset, end=group_end, field="record size")
    _require(size >= 4, f"record at offset {offset} has size below 4")
    end = offset + size
    _require(end <= group_end, f"record at offset {offset} exceeds group boundary")
    record: dict[str, Any] = {
        "offset": offset,
        "end_offset": end,
        "size": size,
        "subtype": subtype,
        "subtype_hex": f"0x{subtype:02X}",
        "raw_record_base64": base64.b64encode(data[offset:end]).decode("ascii"),
    }
    if subtype == 0x08:
        _require(size >= 13, f"subtype 0x08 record at offset {offset} is too small")
        index = _u32(data, offset + 4, end=end, field="record index")
        flags = _u32(data, offset + 8, end=end, field="record flags")
        raw_region = data[offset + 12 : end]
        _require(
            raw_region.endswith(b"\0"),
            f"subtype 0x08 text at offset {offset + 12} lacks final NUL",
        )
        raw_text = raw_region[:-1]
        record.update(index=index, flags=flags)
        text_offset = offset + 12
    elif subtype == 0x09:
        _require(size >= 73, f"subtype 0x09 record at offset {offset} is too small")
        index = _u32(data, offset + 4, end=end, field="record index")
        reserved = data[offset + 8 : offset + 72]
        raw_region = data[offset + 72 : end]
        _require(
            raw_region.endswith(b"\0"),
            f"subtype 0x09 text at offset {offset + 72} lacks final NUL",
        )
        raw_text = raw_region[:-1]
        record.update(
            index=index,
            reserved_base64=base64.b64encode(reserved).decode("ascii"),
        )
        text_offset = offset + 72
    elif subtype in _TEXT_SUBTYPES:
        _require(size >= 8, f"text record at offset {offset} is too small")
        index = _u32(data, offset + 4, end=end, field="record index")
        raw_region = data[offset + 8 : end]
        raw_text = raw_region.rstrip(b"\0")
        record["index"] = index
        text_offset = offset + 8
    else:
        return record, end
    record.update(
        text=_decode_text(raw_text, offset=text_offset),
        text_encoding="cp936",
        text_offset=text_offset,
        text_end_offset=text_offset + len(raw_text),
        raw_text_base64=base64.b64encode(raw_text).decode("ascii"),
        raw_text_region_base64=base64.b64encode(raw_region).decode("ascii"),
    )
    return record, end


def _parse_group(
    data: bytes, *, offset: int, section_end: int
) -> tuple[dict[str, Any], int]:
    header_end = offset + _GROUP_HEADER.size
    _require(header_end <= section_end, f"group header at offset {offset} is truncated")
    subtype, data_len, count, reserved = _GROUP_HEADER.unpack_from(data, offset)
    end = header_end + data_len
    _require(end <= section_end, f"group at offset {offset} exceeds section boundary")
    records: list[dict[str, Any]] = []
    cursor = header_end
    for _ in range(count):
        record, cursor = _parse_record(
            data,
            offset=cursor,
            group_end=end,
            subtype=subtype,
        )
        records.append(record)
    _require(
        cursor == end,
        f"group at offset {offset} count/data_len mismatch: "
        f"records end at {cursor}, expected {end}",
    )
    return (
        {
            "offset": offset,
            "end_offset": end,
            "header_size": _GROUP_HEADER.size,
            "data_length": data_len,
            "record_count": count,
            "subtype": subtype,
            "subtype_hex": f"0x{subtype:02X}",
            "reserved_base64": base64.b64encode(reserved).decode("ascii"),
            "records": records,
        },
        end,
    )


def _first_section_offset(data: bytes) -> int:
    offsets = [offset for tag in _SECTION_TAGS if (offset := data.find(tag)) >= 0]
    _require(bool(offsets), "payload contains no known MODULE/COMMENT/LABEL section")
    return min(offsets)


def parse_payload(data: bytes) -> dict[str, Any]:
    """Parse one complete payload, rejecting all truncated or inconsistent sizes."""
    _require(bool(data), "payload is empty")
    sections: list[dict[str, Any]] = []
    cursor = _first_section_offset(data)
    global_header = data[:cursor]
    while cursor < len(data):
        header_end = cursor + _SECTION_HEADER.size
        _require(header_end <= len(data), f"section header at offset {cursor} is truncated")
        raw_tag, version, section_size = _SECTION_HEADER.unpack_from(data, cursor)
        _require(
            raw_tag in _SECTION_TAGS,
            f"unknown section tag at offset {cursor}: {raw_tag!r}",
        )
        _require(
            section_size >= _SECTION_HEADER.size,
            f"section at offset {cursor} is smaller than its header",
        )
        end = cursor + section_size
        _require(end <= len(data), f"section at offset {cursor} exceeds payload boundary")
        tag = _SECTION_TAGS[raw_tag]
        groups: list[dict[str, Any]] = []
        group_cursor = header_end
        while group_cursor < end:
            group, group_cursor = _parse_group(
                data,
                offset=group_cursor,
                section_end=end,
            )
            groups.append(group)
        _require(group_cursor == end, f"section at offset {cursor} did not end exactly")
        sections.append(
            {
                "offset": cursor,
                "end_offset": end,
                "size": section_size,
                "tag": tag,
                "raw_tag_base64": base64.b64encode(raw_tag).decode("ascii"),
                "version": version,
                "groups": groups,
            }
        )
        cursor = end
    _require(cursor == len(data), "payload has unparsed trailing bytes")
    return {
        "schema_version": 1,
        "format": "CF_KV_STUDIO_2",
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "global_header": {
            "offset": 0,
            "size": len(global_header),
            "sha256": hashlib.sha256(global_header).hexdigest(),
            "base64": base64.b64encode(global_header).decode("ascii"),
        },
        "sections": sections,
    }


def parse_file(path: Path) -> dict[str, Any]:
    result = parse_payload(path.read_bytes())
    result["source_file"] = path.name
    return result


def _destination(source: Path, *, input_root: Path, output: Path | None) -> Path:
    name = source.name + ".parsed.json"
    if input_root.is_file():
        return output or source.with_name(name)
    relative = source.relative_to(input_root)
    root = output or input_root
    return root / relative.parent / name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="one .bin file or a directory")
    parser.add_argument("--output", type=Path, help="output file or directory")
    args = parser.parse_args(argv)
    source: Path = args.input
    _require(source.exists(), f"input does not exist: {source}")
    files = [source] if source.is_file() else sorted(source.rglob("*.bin"))
    _require(bool(files), f"no .bin files found under: {source}")
    if source.is_dir() and args.output is not None:
        args.output.mkdir(parents=True, exist_ok=True)
    for path in files:
        destination = _destination(path, input_root=source, output=args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(parse_file(path), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
