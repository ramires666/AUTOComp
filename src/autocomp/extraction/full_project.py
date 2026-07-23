"""Build one lossless JSON catalog from the project tree and mnemonic exports."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_HEADER = re.compile(
    r"^\s*;?\s*(?:program(?:\s+name)?|module)\s*[:：]\s*(?P<name>.+?)\s*$",
    re.IGNORECASE,
)
_OPCODE = re.compile(r"^\s*(?P<opcode>[A-Za-z_][A-Za-z0-9_.]*)")
_SLOTS = ("original", "current", "english", "russian")
_PROGRAM_SYSTEM_CHILDREN = frozenset({"局部标号", "书签"})


def _decode(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", raw.decode("utf-8-sig")
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le", raw.decode("utf-16")
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be", raw.decode("utf-16")
    if b"\x00" in raw[:4096]:
        even_nulls = raw[:4096:2].count(0)
        odd_nulls = raw[1:4096:2].count(0)
        encoding = "utf-16-le" if odd_nulls > even_nulls else "utf-16-be"
        try:
            return encoding, raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValueError("mnemonic export has undecodable NUL bytes") from exc
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return encoding, raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("mnemonic export encoding is unsupported")


def _code_without_comments(line: str, in_block: bool) -> tuple[str, bool]:
    output: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(line):
        if in_block:
            end = line.find("*/", index)
            if end < 0:
                return "".join(output), True
            index = end + 2
            in_block = False
            continue
        char = line[index]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            output.append(char)
            index += 1
        elif line.startswith("/*", index):
            in_block = True
            index += 2
        elif line.startswith("//", index):
            break
        else:
            output.append(char)
            index += 1
    return "".join(output), in_block


def _parsed_content(
    *,
    raw: bytes,
    text: str,
    encoding: str,
    file: str,
    capture: dict[str, Any],
) -> dict[str, Any]:
    physical_lines = text.splitlines(keepends=True)
    lines: list[str] = []
    line_records: list[dict[str, Any]] = []
    newline_styles: set[str] = set()
    for number, physical in enumerate(physical_lines, 1):
        if physical.endswith("\r\n"):
            line, eol = physical[:-2], "CRLF"
        elif physical.endswith("\n"):
            line, eol = physical[:-1], "LF"
        elif physical.endswith("\r"):
            line, eol = physical[:-1], "CR"
        else:
            line, eol = physical, ""
        lines.append(line)
        line_records.append({"number": number, "text": line, "eol": eol})
        if eol:
            newline_styles.add(eol)
    commands: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    in_block = False
    for number, line in enumerate(lines, 1):
        for match in _CJK.finditer(line):
            occurrences.append(
                {"line": number, "column": match.start() + 1, "text": match.group(0)}
            )
        code, in_block = _code_without_comments(line, in_block)
        code = code.strip()
        if code:
            opcode_match = _OPCODE.match(code)
            commands.append(
                {
                    "line": number,
                    "text": code,
                    "opcode": opcode_match.group("opcode") if opcode_match else "",
                }
            )
    return {
        "file": file,
        "capture": capture,
        "raw_bytes_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes_size": len(raw),
        "raw_bytes_base64": base64.b64encode(raw).decode("ascii"),
        "encoding": encoding,
        "raw_text": text,
        "newline_styles": sorted(newline_styles),
        "terminal_newline": bool(physical_lines and physical_lines[-1] != lines[-1]),
        "lines": line_records,
        "commands": commands,
        "cjk_occurrences": occurrences,
    }


def _content(path: Path, root: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    encoding, text = _decode(raw)
    relative = path.relative_to(root).as_posix()
    return _parsed_content(
        raw=raw,
        text=text,
        encoding=encoding,
        file=relative,
        capture={"method": "mnemonic_export", "source": relative},
    )


def _state_content(
    root: Path,
    *,
    record_id: str,
    record: dict[str, Any],
) -> tuple[tuple[int, ...], dict[str, Any]]:
    raw_locator = record.get("locator")
    if (
        not isinstance(raw_locator, list)
        or not raw_locator
        or any(isinstance(part, bool) or not isinstance(part, int) for part in raw_locator)
    ):
        raise ValueError("program record has an invalid locator")
    if record.get("status") != "complete":
        raise ValueError("program record is not complete")
    selected = record.get("selected_attempt")
    attempts = record.get("attempts")
    if (
        isinstance(selected, bool)
        or not isinstance(selected, int)
        or not isinstance(attempts, list)
        or not 0 <= selected < len(attempts)
        or not isinstance(attempts[selected], dict)
    ):
        raise ValueError("program record has no valid selected_attempt")
    attempt = attempts[selected]
    relative = Path(str(attempt.get("text_file", "")))
    root_resolved = root.resolve()
    path = (root / relative).resolve()
    try:
        safe_relative = path.relative_to(root_resolved).as_posix()
    except ValueError:
        raise ValueError("selected text_file escapes the content directory") from None
    if not path.is_file():
        raise ValueError("selected text_file does not exist")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if attempt.get("sha256") != digest:
        raise ValueError("selected text_file SHA-256 does not match state")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("selected text_file is not valid UTF-8") from None
    return tuple(raw_locator), _parsed_content(
        raw=raw,
        text=text,
        encoding="utf-8",
        file=safe_relative,
        capture={
            "method": str(attempt.get("method", "")),
            "source": "extract-kvstudio-full-content",
            "state_file": "state.json",
            "program_record_id": record_id,
            "selected_attempt": selected,
            "text_file": safe_relative,
            "captured_at": attempt.get("captured_at"),
        },
    )


def _identifier(name: str) -> str:
    return name.split(":", 1)[0].strip()


def _key(value: str) -> str:
    return value.strip().casefold()


def _canonical(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _tree_names_and_paths(
    record: dict[str, Any], records: dict[tuple[int, ...], dict[str, Any]]
) -> dict[str, Any]:
    locator = tuple(record["locator"])
    prefixes = [locator[:depth] for depth in range(1, len(locator) + 1)]
    return {
        "names": {
            "original": record["original_name"],
            "current": record["current_tree_name"],
            "english": record["english_name"],
            "russian": record["russian_name"] or None,
        },
        "paths": {
            "original": record["original_path"],
            "current": [records[prefix]["current_tree_name"] for prefix in prefixes],
            "english": record["english_path"],
            "russian": [records[prefix]["russian_name"] or None for prefix in prefixes],
        },
    }


def _tree_map(
    record: dict[str, Any], records: dict[tuple[int, ...], dict[str, Any]]
) -> dict[str, Any]:
    return {
        "locator": list(record["locator"]),
        "translation_record_id": record.get("translation_record_id"),
        "english_status": record["english_status"],
        **_tree_names_and_paths(record, records),
    }


def _tree_hierarchy(
    raw_records: list[dict[str, Any]],
    records: dict[tuple[int, ...], dict[str, Any]],
) -> list[dict[str, Any]]:
    nodes = {
        locator: {
            "locator": list(locator),
            "category": record["category"],
            "status": record["english_status"],
            "translation_record_id": record.get("translation_record_id"),
            **_tree_names_and_paths(record, records),
            "children": [],
        }
        for locator, record in records.items()
    }
    roots: list[dict[str, Any]] = []
    for record in raw_records:
        locator = tuple(record["locator"])
        node = nodes[locator]
        parent = nodes.get(locator[:-1])
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


def _program_records(
    raw_records: list[dict[str, Any]],
    records: dict[tuple[int, ...], dict[str, Any]],
) -> list[dict[str, Any]]:
    system_children: dict[tuple[int, ...], set[str]] = {}
    for record in raw_records:
        locator = tuple(record["locator"])
        if locator and record.get("category") == "system_ui":
            system_children.setdefault(locator[:-1], set()).add(
                str(record.get("original_name", ""))
            )
    locators = {
        parent
        for parent, names in system_children.items()
        if _PROGRAM_SYSTEM_CHILDREN.issubset(names) and parent in records
    }
    locators.update(
        tuple(record["locator"]) for record in raw_records if record.get("category") == "program"
    )
    return [records[locator] for locator in sorted(locators)]


def build_full_project_catalog(
    tree_catalog: dict[str, Any],
    export_directory: Path,
    *,
    content_slot: str = "current",
) -> dict[str, Any]:
    """Merge captured program text without discarding unmatched tree nodes or files."""
    if content_slot not in _SLOTS:
        raise ValueError(f"content slot must be one of: {', '.join(_SLOTS)}")
    raw_records = tree_catalog.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("tree catalog records must be an array")
    records = {tuple(record["locator"]): record for record in raw_records}
    if len(records) != len(raw_records):
        raise ValueError("tree catalog contains duplicate locators")
    program_records = _program_records(raw_records, records)
    if not program_records:
        raise ValueError("tree catalog contains no program records")

    alias_index: dict[str, set[tuple[int, ...]]] = {}
    canonical_index: dict[str, set[tuple[int, ...]]] = {}
    for record in program_records:
        locator = tuple(record["locator"])
        for field in ("original_name", "current_tree_name", "english_name"):
            alias = _identifier(str(record[field]))
            alias_index.setdefault(_key(alias), set()).add(locator)
            canonical_index.setdefault(_canonical(alias), set()).add(locator)

    mapped: dict[tuple[int, ...], dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    exports: list[tuple[Path, dict[str, Any]]] = []
    state_path = export_directory / "state.json"
    source_mode = "extractor_state" if state_path.is_file() else "mnemonic_exports"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state_programs = state.get("programs") if isinstance(state, dict) else None
        if not isinstance(state_programs, dict):
            raise ValueError("extractor state has no programs object")
        program_locators = {tuple(record["locator"]) for record in program_records}
        for raw_record_id, raw_record in state_programs.items():
            record_id = str(raw_record_id)
            if not isinstance(raw_record, dict):
                unmatched.append(
                    {
                        "reason": "invalid_state_program_record",
                        "program_record_id": record_id,
                        "error": "record is not an object",
                    }
                )
                continue
            try:
                locator, content = _state_content(
                    export_directory,
                    record_id=record_id,
                    record=raw_record,
                )
            except ValueError as exc:
                unmatched.append(
                    {
                        "reason": "invalid_state_program_record",
                        "program_record_id": record_id,
                        "locator": raw_record.get("locator"),
                        "error": str(exc),
                    }
                )
                continue
            reason = ""
            if locator not in program_locators:
                reason = "no_program_locator_match"
            elif locator in mapped:
                reason = "duplicate_capture_for_program"
            else:
                mapped[locator] = content
            if reason:
                unmatched.append(
                    {
                        "reason": reason,
                        "program_record_id": record_id,
                        "locator": list(locator),
                        "content": content,
                    }
                )
    else:
        for path in sorted(
            export_directory.rglob("*"), key=lambda item: item.as_posix().casefold()
        ):
            if path.is_file() and path.suffix.casefold() == ".mnm":
                exports.append((path, _content(path, export_directory)))
        for path, content in exports:
            candidates = set(alias_index.get(_key(path.stem), ()))
            if not candidates:
                candidates = set(canonical_index.get(_canonical(path.stem), ()))
            if not candidates:
                for line in content["raw_text"].splitlines()[:50]:
                    match = _HEADER.match(line)
                    if match:
                        candidates.update(alias_index.get(_key(match.group("name")), ()))
            reason = ""
            if len(candidates) != 1:
                reason = "no_unique_program_match"
            else:
                locator = next(iter(candidates))
                if locator in mapped:
                    reason = "duplicate_export_for_program"
                else:
                    mapped[locator] = content
            if reason:
                unmatched.append({"reason": reason, "content": content})

    programs: list[dict[str, Any]] = []
    for record in sorted(program_records, key=lambda item: tuple(item["locator"])):
        locator = tuple(record["locator"])
        slots = {slot: None for slot in _SLOTS}
        slots[content_slot] = mapped.get(locator)
        programs.append({"tree_map": _tree_map(record, records), "content": slots})

    missing = [
        program["tree_map"]["locator"]
        for program in programs
        if program["content"][content_slot] is None
    ]
    cjk_count = sum(len(content["cjk_occurrences"]) for content in mapped.values()) + sum(
        len(item.get("content", {}).get("cjk_occurrences", [])) for item in unmatched
    )
    complete = not missing and not unmatched
    return {
        "schema_version": 1,
        "artifact_type": "full_project_multilingual_catalog",
        "content_slot": content_slot,
        "content_source_mode": source_mode,
        "complete": complete,
        "summary": {
            "tree_nodes": len(raw_records),
            "tree_programs": len(program_records),
            "mnemonic_exports": len(exports),
            "mapped_programs": len(mapped),
            "missing_programs": len(missing),
            "unmatched_exports": len(unmatched),
            "cjk_occurrences": cjk_count,
        },
        "tree": tree_catalog,
        "tree_hierarchy": _tree_hierarchy(raw_records, records),
        "programs": programs,
        "missing_program_locators": missing,
        "unmatched_exports": unmatched,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree_catalog", type=Path)
    parser.add_argument("export_directory", type=Path)
    parser.add_argument("--slot", choices=_SLOTS, default="current")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    tree = json.loads(args.tree_catalog.read_text(encoding="utf-8"))
    result = build_full_project_catalog(tree, args.export_directory, content_slot=args.slot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
