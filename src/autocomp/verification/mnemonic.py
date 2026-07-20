"""Normalization and comparison of mnemonic-list exports, not project binaries."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


def _without_comment(line: str, *, semicolon_comments: bool) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        # KV operands use ``#`` for decimal constants (for example ``#90``).
        # Semicolon is configurable because the real 11.62 export must prove
        # whether it is a comment marker or meaningful syntax.
        elif (semicolon_comments and char == ";") or line.startswith("//", index):
            return line[:index]
    return line


def _normalize_whitespace(line: str) -> str:
    """Collapse layout whitespace only outside behavior-affecting literals."""

    output: list[str] = []
    quote: str | None = None
    escaped = False
    pending_space = False
    for char in line.strip():
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            if pending_space and output:
                output.append(" ")
            pending_space = False
            quote = char
            output.append(char)
        elif char.isspace():
            pending_space = True
        else:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            output.append(char)
    return "".join(output)


def normalize_mnemonic_export(export: str, *, semicolon_comments: bool = False) -> str:
    """Remove comments and layout-only differences while preserving instruction text."""
    normalized: list[str] = []
    in_block_comment = False
    for raw_line in export.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line, in_block_comment = _without_block_comments(raw_line, in_block_comment)
        logic = _normalize_whitespace(_without_comment(line, semicolon_comments=semicolon_comments))
        if logic:
            normalized.append(logic)
    return "\n".join(normalized) + ("\n" if normalized else "")


def _without_block_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Remove /* ... */ text without interpreting markers inside strings."""

    output: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(line):
        if in_comment:
            end = line.find("*/", index)
            if end < 0:
                return "".join(output), True
            index = end + 2
            in_comment = False
            output.append(" ")
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
        if char in ("'", '"'):
            quote = char
            output.append(char)
            index += 1
            continue
        if line.startswith("/*", index):
            in_comment = True
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output), in_comment


@dataclass(frozen=True, slots=True)
class MnemonicComparison:
    baseline_sha256: str
    candidate_sha256: str
    identical: bool
    baseline_normalized: str
    candidate_normalized: str


def compare_mnemonic_exports(
    baseline: str,
    candidate: str,
    *,
    semicolon_comments: bool = False,
) -> MnemonicComparison:
    before = normalize_mnemonic_export(baseline, semicolon_comments=semicolon_comments)
    after = normalize_mnemonic_export(candidate, semicolon_comments=semicolon_comments)
    return MnemonicComparison(
        baseline_sha256=sha256(before.encode("utf-8")).hexdigest(),
        candidate_sha256=sha256(after.encode("utf-8")).hexdigest(),
        identical=before == after,
        baseline_normalized=before,
        candidate_normalized=after,
    )
