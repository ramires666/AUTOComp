"""Protect non-translatable PLC fragments before provider calls."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Preserve every ASCII identifier/number embedded in Chinese project text. The
# inventory contains command names, device addresses, protocol names, station
# numbers, and deliberately odd placeholders such as ``DM??`` and
# ``MR--DM102``. Treating only known PLC prefixes as protected would let a
# model silently normalize an unknown but behavior-significant token.
_ASCII_ATOM = r"""
(?:
    @?[A-Za-z0-9_?]+(?:\.[A-Za-z0-9_?]+)*(?:[#%])?
  | \#\d+(?:\.\d+)*
)
"""
_CONNECTOR = r"(?:--|[-.~=/+:])"
_TOKEN_RE = re.compile(
    rf"""
    /\* | \*/
  | (?<![A-Za-z0-9_?@#])
      (?:{_ASCII_ATOM})(?:{_CONNECTOR}(?:{_ASCII_ATOM}))*(?:\.{{2,}})?
    (?![A-Za-z0-9_?@#])
  | [+\-_=~/.]+
  | [#@→]
    """,
    re.ASCII | re.VERBOSE,
)
_MARKER_RE = re.compile(r"\[\[PLC_TOKEN_(\d+)\]\]")
_MARKER_PREFIX = "[[PLC_TOKEN_"


@dataclass(frozen=True, slots=True)
class ProtectedText:
    text: str
    tokens: tuple[str, ...]


def protect_tokens(text: str) -> ProtectedText:
    if _MARKER_PREFIX in text:
        raise ValueError("source text contains the reserved PLC token marker prefix")
    tokens: list[str] = []

    def replace(match: re.Match[str]) -> str:
        # ``K金`` is the Chinese metallurgy term for karat gold, not a PLC
        # identifier. Leave its K visible so the phrase can become
        # ``Karat Gold`` while standalone ASCII identifiers stay immutable.
        if match.group(0) == "K" and text[match.end() :].startswith("金"):
            return match.group(0)
        tokens.append(match.group(0))
        return f"[[PLC_TOKEN_{len(tokens) - 1}]]"

    return ProtectedText(_TOKEN_RE.sub(replace, text), tuple(tokens))


def restore_tokens(text: str, tokens: tuple[str, ...]) -> str:
    marker_ids = [int(match.group(1)) for match in _MARKER_RE.finditer(text)]
    if marker_ids != list(range(len(tokens))):
        raise ValueError("protected token markers are missing, duplicated, or reordered")
    restored = _MARKER_RE.sub(lambda match: tokens[int(match.group(1))], text)
    if _MARKER_PREFIX in restored:
        raise ValueError("translation contains an unknown protected token marker")
    return restored


def validate_protected_tokens(source: str, target: str) -> tuple[str, ...]:
    """Require protected source fragments to survive in order and stay separated.

    Exact glossary and translation-memory hits do not pass through marker
    restoration. This validation gives those paths the same fail-closed token
    guarantees as provider output and also catches a provider removing a
    delimiter between two protected fragments (for example ``@MR0、@DM0``
    becoming ``@MR0@DM0``).
    """
    matches = [
        match
        for match in _TOKEN_RE.finditer(source)
        if not (match.group(0) == "K" and source[match.end() :].startswith("金"))
    ]
    tokens = tuple(match.group(0) for match in matches)
    target_spans: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        start = target.find(token, cursor)
        if start < 0:
            raise ValueError(f"protected source token missing or reordered: {token!r}")
        end = start + len(token)
        target_spans.append((start, end))
        cursor = end

    for index in range(len(matches) - 1):
        source_gap = source[matches[index].end() : matches[index + 1].start()]
        target_gap = target[target_spans[index][1] : target_spans[index + 1][0]]
        if source_gap and not target_gap:
            raise ValueError(
                "protected source tokens were joined after translation: "
                f"{tokens[index]!r}, {tokens[index + 1]!r}"
            )

    for match, (target_start, target_end) in zip(matches, target_spans, strict=True):
        token = match.group(0)
        source_left = source[match.start() - 1] if match.start() else ""
        source_right = source[match.end()] if match.end() < len(source) else ""
        target_left = target[target_start - 1] if target_start else ""
        target_right = target[target_end] if target_end < len(target) else ""
        left_is_identifier_edge = token[0].isalnum() or token[0] in "@#"
        right_is_identifier_edge = token[-1].isalnum() or token[-1] == "#"
        extends_karat_term = token.endswith("K") and source_right == "金"
        if (
            contains_cjk_character(source_left)
            and left_is_identifier_edge
            and _is_ascii_identifier_character(target_left)
        ):
            raise ValueError(f"protected source token joined on the left: {token!r}")
        if (
            contains_cjk_character(source_right)
            and right_is_identifier_edge
            and not extends_karat_term
            and _is_ascii_identifier_character(target_right)
        ):
            raise ValueError(f"protected source token joined on the right: {token!r}")
    return tokens


def separate_protected_tokens(source: str, target: str) -> str:
    """Insert deterministic delimiters when provider prose touches PLC tokens.

    Providers translate marker-separated Chinese segments independently and
    sometimes return ``11#Open`` or ``XYZAxis``. The source determines where
    an English word boundary is required; technical token spelling and order
    are never changed.
    """
    matches = [
        match
        for match in _TOKEN_RE.finditer(source)
        if not (match.group(0) == "K" and source[match.end() :].startswith("金"))
    ]
    target_spans: list[tuple[int, int]] = []
    cursor = 0
    for match in matches:
        token = match.group(0)
        start = target.find(token, cursor)
        if start < 0:
            return target
        end = start + len(token)
        target_spans.append((start, end))
        cursor = end

    insertions: dict[int, str] = {}
    blocked_positions: set[int] = set()
    for index in range(len(matches) - 1):
        source_gap = source[matches[index].end() : matches[index + 1].start()]
        gap_start = target_spans[index][1]
        gap_end = target_spans[index + 1][0]
        if source_gap and gap_start == gap_end:
            if contains_cjk_character(source_gap):
                blocked_positions.add(gap_start)
            else:
                if "；" in source_gap or ";" in source_gap:
                    delimiter = "; "
                elif "、" in source_gap or "，" in source_gap or "," in source_gap:
                    delimiter = ", "
                else:
                    delimiter = " "
                insertions[gap_start] = delimiter

    for match, (target_start, target_end) in zip(matches, target_spans, strict=True):
        token = match.group(0)
        source_left = source[match.start() - 1] if match.start() else ""
        source_right = source[match.end()] if match.end() < len(source) else ""
        target_left = target[target_start - 1] if target_start else ""
        target_right = target[target_end] if target_end < len(target) else ""
        if (
            contains_cjk_character(source_left)
            and (token[0].isalnum() or token[0] in "@#")
            and _is_ascii_identifier_character(target_left)
            and target_start not in blocked_positions
        ):
            insertions.setdefault(target_start, " ")
        if (
            contains_cjk_character(source_right)
            and (token[-1].isalnum() or token[-1] == "#")
            and not (token.endswith("K") and source_right == "金")
            and _is_ascii_identifier_character(target_right)
            and target_end not in blocked_positions
        ):
            insertions.setdefault(target_end, " ")

    result = target
    for position in sorted(insertions, reverse=True):
        result = result[:position] + insertions[position] + result[position:]
    return result


def contains_cjk_character(value: str) -> bool:
    return bool(value) and any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        for character in value
    )


def _is_ascii_identifier_character(value: str) -> bool:
    return bool(value) and value.isascii() and (value.isalnum() or value == "_")
