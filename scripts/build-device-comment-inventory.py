"""Build a deterministic device-comment translation inventory from KV exports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMMENTS = ROOT / "comments-export" / "original-comments.csv"
DEFAULT_MNEMONICS = ROOT / "mnemonic-export" / "AUTOComp_mnemonic_nocomment_20260722"
DEFAULT_OUTPUT = ROOT / ".autocomp" / "device-comment-inventory.json"

_ADDRESS_RE = re.compile(r"([A-Za-z]+)(\d+)")
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_MODULE_PREFIX = ";MODULE:"
_METADATA_PREFIXES = (";MODULE:", ";MODULE_TYPE:", ";SCRIPT_TYPE:")


class InventoryError(ValueError):
    """The input exports do not satisfy the expected lossless shape."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_address(address: str) -> str:
    match = _ADDRESS_RE.fullmatch(address)
    if match is None:
        raise InventoryError(f"unsupported device address: {address!r}")
    prefix, number = match.groups()
    return f"{prefix.upper()}{int(number)}"


def _address_prefix(address: str) -> str:
    match = _ADDRESS_RE.fullmatch(address)
    if match is None:
        raise InventoryError(f"unsupported device address: {address!r}")
    return match.group(1).upper()


def _read_comment_rows(path: Path) -> tuple[bytes, list[list[str]]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("cp936")
    except UnicodeDecodeError as exc:
        raise InventoryError(f"device-comment CSV is not valid CP936: {path}") from exc

    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise InventoryError("device-comment CSV is empty")

    seen_exact: set[str] = set()
    seen_canonical: set[str] = set()
    for row_index, row in enumerate(rows, start=1):
        if len(row) != 5:
            raise InventoryError(
                f"device-comment CSV row {row_index} has {len(row)} columns, expected 5"
            )
        address, empty_1, source_text, empty_3, empty_4 = row
        if not address:
            raise InventoryError(f"device-comment CSV row {row_index} has no address")
        if empty_1 or empty_3 or empty_4:
            raise InventoryError(
                f"device-comment CSV row {row_index} has data outside columns 1 and 3"
            )
        if not source_text:
            raise InventoryError(f"device-comment CSV row {row_index} has no comment")
        canonical = _canonical_address(address)
        if address in seen_exact:
            raise InventoryError(f"duplicate exact device address at row {row_index}: {address}")
        if canonical in seen_canonical:
            raise InventoryError(
                f"duplicate canonical device address at row {row_index}: {address}"
            )
        seen_exact.add(address)
        seen_canonical.add(canonical)
    return raw, rows


def _device_token_pattern(prefixes: Iterable[str]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(prefix) for prefix in sorted(set(prefixes), key=lambda item: (-len(item), item))
    )
    return re.compile(
        rf"(?<![A-Za-z0-9_])(?P<indirect>@?)(?P<prefix>{alternatives})"
        rf"(?P<number>\d+)(?:\.(?P<suffix>[A-Za-z]+))?",
        re.IGNORECASE,
    )


def _is_heading(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith(";") or stripped.startswith(_METADATA_PREFIXES):
        return False
    body = stripped[1:].strip()
    if not body:
        return False
    return (
        "<h1/>" in body
        or ("/*" in body and "*/" in body)
        or body.startswith(("//", "'"))
        or (body.startswith("-") and any(char.isalpha() or _CJK_RE.match(char) for char in body))
    )


def _read_mnemonic_file(path: Path) -> tuple[bytes, list[str]]:
    raw = path.read_bytes()
    try:
        return raw, raw.decode("cp936").splitlines()
    except UnicodeDecodeError as exc:
        raise InventoryError(f"mnemonic export is not valid CP936: {path}") from exc


def _build_context_index(
    mnemonic_dir: Path,
    address_keys: set[str],
    prefixes: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    token_pattern = _device_token_pattern(prefixes)
    contexts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    file_metadata: list[dict[str, Any]] = []

    mnemonic_paths = sorted(mnemonic_dir.glob("*.mnm"), key=lambda path: path.name.casefold())
    if not mnemonic_paths:
        raise InventoryError(f"no .mnm files found in {mnemonic_dir}")

    for path in mnemonic_paths:
        raw, lines = _read_mnemonic_file(path)
        module_name = next(
            (line[len(_MODULE_PREFIX) :] for line in lines if line.startswith(_MODULE_PREFIX)),
            path.stem,
        )
        file_metadata.append(
            {
                "file": path.name,
                "module": module_name,
                "byte_size": len(raw),
                "sha256": _sha256(raw),
                "encoding": "cp936",
            }
        )

        nearest_heading: dict[str, Any] | None = None
        for line_number, line in enumerate(lines, start=1):
            if _is_heading(line):
                nearest_heading = {"line_number": line_number, "text": line}
            if line.lstrip().startswith(";"):
                continue

            scan_text = _QUOTED_RE.sub('""', line)
            for match in token_pattern.finditer(scan_text):
                canonical = f"{match.group('prefix').upper()}{int(match.group('number'))}"
                if canonical not in address_keys:
                    continue
                contexts[canonical].append(
                    {
                        "module_file": path.name,
                        "module": module_name,
                        "line_number": line_number,
                        "instruction": line,
                        "matched_token": match.group(0),
                        "indirect": bool(match.group("indirect")),
                        "suffix": match.group("suffix") or "",
                        "nearest_heading": nearest_heading,
                    }
                )
    return dict(contexts), file_metadata


def _adjacent_row(
    rows: list[list[str]], row_index: int, offset: int, prefix: str
) -> dict[str, Any] | None:
    adjacent_index = row_index - 1 + offset
    if not 0 <= adjacent_index < len(rows):
        return None
    adjacent = rows[adjacent_index]
    if _address_prefix(adjacent[0]) != prefix:
        return None
    return {
        "row_index": adjacent_index + 1,
        "address": adjacent[0],
        "source_text": adjacent[2],
    }


def _distinct_module_contexts(
    grouped_rows: list[dict[str, Any]], context_index: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    seen_modules: set[str] = set()
    occurrence_count = sum(
        len(context_index.get(row["canonical_address"], [])) for row in grouped_rows
    )
    for row in grouped_rows:
        occurrences = context_index.get(row["canonical_address"], [])
        for occurrence in occurrences:
            module_key = occurrence["module_file"]
            if module_key in seen_modules:
                continue
            selected.append({"address": row["address"], **occurrence})
            seen_modules.add(module_key)
            if len(selected) == 3:
                return selected, occurrence_count
    return selected, occurrence_count


def build_inventory(comments_path: Path, mnemonic_dir: Path) -> dict[str, Any]:
    raw, csv_rows = _read_comment_rows(comments_path)
    canonical_addresses = {_canonical_address(row[0]) for row in csv_rows}
    prefixes = {_address_prefix(row[0]) for row in csv_rows}
    context_index, mnemonic_files = _build_context_index(
        mnemonic_dir, canonical_addresses, prefixes
    )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row_index, row in enumerate(csv_rows, start=1):
        address, _, source_text, _, _ = row
        prefix = _address_prefix(address)
        row_record = {
            "row_index": row_index,
            "address": address,
            "canonical_address": _canonical_address(address),
            "adjacent_same_prefix": {
                "previous": _adjacent_row(csv_rows, row_index, -1, prefix),
                "next": _adjacent_row(csv_rows, row_index, 1, prefix),
            },
        }
        grouped.setdefault(source_text, []).append(row_record)

    sources: list[dict[str, Any]] = []
    rows_with_logic_context = 0
    for source_text, grouped_rows in grouped.items():
        selected_contexts, occurrence_count = _distinct_module_contexts(
            grouped_rows, context_index
        )
        rows_with_logic_context += sum(
            bool(context_index.get(row["canonical_address"])) for row in grouped_rows
        )
        sources.append(
            {
                "source_text": source_text,
                "source_cp936_bytes": len(source_text.encode("cp936")),
                "contains_cjk": bool(_CJK_RE.search(source_text)),
                "rows": grouped_rows,
                "logic_occurrence_count": occurrence_count,
                "contexts": selected_contexts,
            }
        )

    return {
        "schema_version": 1,
        "artifact_type": "device_comment_translation_inventory",
        "source": {
            "file": comments_path.as_posix(),
            "byte_size": len(raw),
            "sha256": _sha256(raw),
            "encoding": "cp936",
            "bom": False,
            "newline": "CRLF",
            "terminal_newline": raw.endswith(b"\r\n"),
            "has_header": False,
            "column_count": 5,
            "comment_column_zero_based": 2,
        },
        "mnemonic_source": {
            "directory": mnemonic_dir.as_posix(),
            "file_count": len(mnemonic_files),
            "files": mnemonic_files,
        },
        "summary": {
            "row_count": len(csv_rows),
            "unique_address_count": len(canonical_addresses),
            "unique_source_count": len(sources),
            "cjk_row_count": sum(bool(_CJK_RE.search(row[2])) for row in csv_rows),
            "cjk_source_count": sum(source["contains_cjk"] for source in sources),
            "rows_with_logic_context": rows_with_logic_context,
            "addresses_with_logic_context": len(context_index),
        },
        "sources": sources,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comments", type=Path, default=DEFAULT_COMMENTS)
    parser.add_argument("--mnemonic-dir", type=Path, default=DEFAULT_MNEMONICS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    inventory = build_inventory(args.comments, args.mnemonic_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": inventory["summary"]["row_count"],
                "unique_sources": inventory["summary"]["unique_source_count"],
                "contexts": inventory["summary"]["addresses_with_logic_context"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
