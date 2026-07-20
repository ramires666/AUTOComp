"""Extract translatable comments from mnemonic-list text exports.

The parser deliberately does not attempt to modify an export or a KV project file.
Unknown CJK-bearing lines are returned as review-required fallback records instead of
being guessed as comments.
"""

from __future__ import annotations

import re
from dataclasses import replace
from hashlib import sha256

from autocomp.translation.inventory import contains_cjk, with_assessed_risk
from autocomp.translation.models import InventoryRecord, TextKind

_HEADING = re.compile(
    r"^\s*(?P<label>program(?:\s+name)?|section|folder|task|network)\s*[:：]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_LOGIC = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_.]*\b")


def _record_id(source_name: str, line: int, kind: TextKind, text: str) -> str:
    material = f"{source_name}\x1f{line}\x1f{kind.value}\x1f{text}".encode()
    return sha256(material).hexdigest()[:20]


def _make_record(
    source_name: str,
    line: int,
    text: str,
    kind: TextKind,
    hierarchy: tuple[str, ...],
    context: str,
) -> InventoryRecord:
    record = InventoryRecord(
        record_id=_record_id(source_name, line, kind, text),
        source_text=text,
        kind=kind,
        hierarchy=hierarchy,
        context=context,
        location=f"{source_name}:{line}" if source_name else f"line:{line}",
    )
    assessed = with_assessed_risk(record)
    # A line whose mnemonic grammar is unknown must never be eligible for a
    # blind replacement, even when its text otherwise looks low-risk.
    if kind is TextKind.OTHER:
        return replace(assessed, requires_review=True)
    return assessed


def _heading_value(text: str) -> tuple[str, str] | None:
    match = _HEADING.match(text)
    if not match:
        return None
    return match.group("label").casefold(), match.group("value")


def _quoted_strings(code: str) -> list[str]:
    """Return quoted values from mnemonic logic, honoring backslash escapes."""

    values: list[str] = []
    quote: str | None = None
    escaped = False
    current: list[str] = []
    for char in code:
        if quote is None:
            if char in ("'", '"'):
                quote = char
                current = []
            continue
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == quote:
            values.append("".join(current))
            quote = None
            current = []
        else:
            current.append(char)
    return values


def _comment_text(value: str) -> str:
    return value.strip().strip("*").strip()


def _next_marker(line: str, start: int) -> tuple[int, str] | None:
    """Find a comment delimiter outside quoted string literals."""
    quote: str | None = None
    escaped = False
    for index in range(start, len(line)):
        char = line[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif line.startswith("/*", index):
            return index, "block"
        elif line.startswith("//", index):
            return index, "line"
        elif char == ";":
            return index, "semicolon"
    return None


def extract_mnemonic_inventory(
    export: str, *, source_name: str = "mnemonic.lst"
) -> list[InventoryRecord]:
    """Return CJK-bearing comments and safe fallback raw-line inventory records.

    `/* ... */` comments can span lines.  Heading comments update hierarchy for
    subsequent records.  `//` and `;` are comment markers outside quoted strings;
    hash-prefixed numeric constants remain logic by design.
    """
    records: list[InventoryRecord] = []
    hierarchy: list[str] = []
    in_block = False
    block_start = 0
    block_parts: list[str] = []

    def consume_comment(text: str, line: int, context: str) -> None:
        nonlocal hierarchy
        cleaned = _comment_text(text)
        if not cleaned:
            return
        heading = _heading_value(cleaned)
        if heading:
            label, value = heading
            if label.startswith("program"):
                hierarchy = [f"program: {value}"]
            else:
                hierarchy.append(f"{label}: {value}")
        if contains_cjk(cleaned):
            records.append(
                _make_record(
                    source_name, line, cleaned, TextKind.COMMENT, tuple(hierarchy), context
                )
            )

    lines = export.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line_number, raw in enumerate(lines, start=1):
        position = 0
        code_parts: list[str] = []
        while position < len(raw):
            if in_block:
                end = raw.find("*/", position)
                if end < 0:
                    block_parts.append(raw[position:])
                    position = len(raw)
                    continue
                block_parts.append(raw[position:end])
                consume_comment("\n".join(block_parts), block_start, "block comment")
                block_parts = []
                in_block = False
                position = end + 2
                continue
            marker_info = _next_marker(raw, position)
            if marker_info is None:
                code_parts.append(raw[position:])
                break
            marker, marker_kind = marker_info
            code_parts.append(raw[position:marker])
            if marker_kind == "line":
                consume_comment(raw[marker + 2 :], line_number, "".join(code_parts).strip())
                break
            if marker_kind == "semicolon":
                consume_comment(raw[marker + 1 :], line_number, "".join(code_parts).strip())
                break
            end = raw.find("*/", marker + 2)
            if end < 0:
                in_block = True
                block_start = line_number
                block_parts = [raw[marker + 2 :]]
                break
            consume_comment(raw[marker + 2 : end], line_number, "".join(code_parts).strip())
            position = end + 2

        code = "".join(code_parts).strip()
        for literal in _quoted_strings(code):
            if contains_cjk(literal):
                records.append(
                    _make_record(
                        source_name,
                        line_number,
                        literal,
                        TextKind.STRING_LITERAL,
                        tuple(hierarchy),
                        code,
                    )
                )
        # Known mnemonic lines, including values such as #10, are never treated as comments.
        if code and contains_cjk(code) and not _LOGIC.match(code):
            records.append(
                _make_record(
                    source_name,
                    line_number,
                    code,
                    TextKind.OTHER,
                    tuple(hierarchy),
                    "unrecognized raw line",
                )
            )
    if in_block:
        # Preserve an unterminated block as an explicitly reviewable raw finding.
        text = "\n".join(block_parts).strip()
        if contains_cjk(text):
            records.append(
                _make_record(
                    source_name,
                    block_start,
                    text,
                    TextKind.OTHER,
                    tuple(hierarchy),
                    "unterminated block comment",
                )
            )
    return records
