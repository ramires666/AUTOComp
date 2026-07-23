"""Build a strict English KV STUDIO device-comment import CSV offline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_ROWS = 4904
EXPECTED_COLUMNS = 5
ADDRESS_COLUMN = 0
COMMENT_COLUMN = 2
EMPTY_COLUMNS = (1, 3, 4)
FORBIDDEN_TARGET_CHARACTERS = {",", '"', "\r", "\n"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _load_rows(path: Path) -> tuple[bytes, list[list[str]]]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("original CSV must not contain a UTF-8 BOM")
    if b"\n" in raw.replace(b"\r\n", b""):
        raise ValueError("original CSV must use CRLF line endings only")
    if not raw.endswith(b"\r\n"):
        raise ValueError("original CSV must end with CRLF")
    try:
        text = raw.decode("cp936", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("original CSV is not valid CP936") from error

    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"invalid original CSV: {error}") from error
    if len(rows) != EXPECTED_ROWS:
        raise ValueError(f"expected {EXPECTED_ROWS} rows, found {len(rows)}")
    for number, row in enumerate(rows, start=1):
        if len(row) != EXPECTED_COLUMNS:
            raise ValueError(
                f"row {number}: expected {EXPECTED_COLUMNS} columns, found {len(row)}"
            )
        if not row[ADDRESS_COLUMN]:
            raise ValueError(f"row {number}: empty device address")
        if not row[COMMENT_COLUMN]:
            raise ValueError(f"row {number}: empty original comment")
        if any(row[index] for index in EMPTY_COLUMNS):
            raise ValueError(f"row {number}: columns 2, 4 and 5 must be empty")
    return raw, rows


def _load_translations(path: Path) -> tuple[bytes, dict[str, str]]:
    raw = path.read_bytes()
    try:
        payload: Any = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"translation map is not valid UTF-8 JSON: {path}") from error

    source = payload.get("translations", payload) if isinstance(payload, dict) else payload
    translations: dict[str, str] = {}
    if isinstance(source, dict):
        items = source.items()
    elif isinstance(source, list):
        normalized: list[tuple[Any, Any]] = []
        for item in source:
            if not isinstance(item, dict):
                raise ValueError("translation list entries must be objects")
            normalized.append(
                (
                    item.get("source", item.get("original")),
                    item.get("english", item.get("target")),
                )
            )
        items = normalized
    else:
        raise ValueError("translations must be a JSON object or array")

    for original, english in items:
        if not isinstance(original, str) or not isinstance(english, str):
            raise ValueError("every translation requires string source and English text")
        if original in translations and translations[original] != english:
            raise ValueError(f"conflicting translations for {original!r}")
        translations[original] = english
    return raw, translations


def _validate_target(source: str, target: str) -> None:
    if not target.strip():
        raise ValueError(f"empty English target for {source!r}")
    if not target.isascii():
        raise ValueError(f"non-ASCII English target for {source!r}: {target!r}")
    encoded = target.encode("ascii")
    if len(target) > 32 or len(encoded) > 32:
        raise ValueError(f"English target exceeds 32 characters/bytes for {source!r}")
    forbidden = sorted(character for character in FORBIDDEN_TARGET_CHARACTERS if character in target)
    if forbidden:
        raise ValueError(
            f"English target contains forbidden CSV characters for {source!r}: {forbidden!r}"
        )


def build(
    original_path: Path,
    translations_path: Path,
) -> tuple[bytes, dict[str, Any]]:
    original_raw, source_rows = _load_rows(original_path)
    translations_raw, translations = _load_translations(translations_path)
    source_counts = Counter(row[COMMENT_COLUMN] for row in source_rows)

    missing = sorted(set(source_counts).difference(translations))
    if missing:
        preview = ", ".join(repr(value) for value in missing[:10])
        raise ValueError(f"missing {len(missing)} translations; first: {preview}")
    for source in source_counts:
        _validate_target(source, translations[source])

    output_rows: list[list[str]] = []
    audit_rows: list[dict[str, Any]] = []
    for number, source_row in enumerate(source_rows, start=1):
        source = source_row[COMMENT_COLUMN]
        target = translations[source]
        output_row = [source_row[ADDRESS_COLUMN], "", target, "", ""]
        output_rows.append(output_row)
        audit_rows.append(
            {
                "row": number,
                "address": source_row[ADDRESS_COLUMN],
                "original": source,
                "english": target,
            }
        )

    # Targets and addresses are validated before joining, so no CSV quoting is needed.
    for number, row in enumerate(output_rows, start=1):
        if any(character in row[ADDRESS_COLUMN] for character in ',"\r\n'):
            raise ValueError(f"row {number}: unsafe device address")
    output_text = "".join(",".join(row) + "\r\n" for row in output_rows)
    output_raw = output_text.encode("ascii", errors="strict")

    # Reparse the exact bytes to assert the deliverable's structural invariants.
    reparsed = list(csv.reader(io.StringIO(output_raw.decode("ascii"), newline=""), strict=True))
    if len(reparsed) != EXPECTED_ROWS or any(len(row) != EXPECTED_COLUMNS for row in reparsed):
        raise AssertionError("generated CSV failed its row/column invariant")
    if [row[ADDRESS_COLUMN] for row in reparsed] != [
        row[ADDRESS_COLUMN] for row in source_rows
    ]:
        raise AssertionError("generated CSV changed device addresses or row order")
    if any(any(row[index] for index in EMPTY_COLUMNS) for row in reparsed):
        raise AssertionError("generated CSV populated a reserved column")

    used_sources = set(source_counts)
    audit: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "kvstudio_device_comments_english_audit",
        "status": "ok",
        "source": {
            "path": str(original_path),
            "encoding": "cp936",
            "newline": "CRLF",
            "bom": False,
            "bytes": len(original_raw),
            "sha256": _sha256(original_raw),
        },
        "translations": {
            "path": str(translations_path),
            "sha256": _sha256(translations_raw),
            "entries": len(translations),
            "used_entries": len(used_sources),
            "unused_sources": sorted(set(translations).difference(used_sources)),
        },
        "output": {
            "encoding": "ascii (CP936-compatible)",
            "newline": "CRLF",
            "bom": False,
            "bytes": len(output_raw),
            "sha256": _sha256(output_raw),
        },
        "invariants": {
            "row_count": len(output_rows),
            "column_count": EXPECTED_COLUMNS,
            "address_column": ADDRESS_COLUMN + 1,
            "comment_column": COMMENT_COLUMN + 1,
            "empty_columns": [index + 1 for index in EMPTY_COLUMNS],
            "addresses_and_order_preserved": True,
            "unique_original_comments": len(source_counts),
            "duplicate_comment_groups": sum(count > 1 for count in source_counts.values()),
            "duplicate_rows_after_first": sum(count - 1 for count in source_counts.values()),
            "all_targets_ascii": True,
            "all_targets_nonempty": True,
            "all_targets_at_most_32_bytes": True,
            "all_duplicate_sources_reuse_target": True,
        },
        "rows": audit_rows,
    }
    return output_raw, audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a strict 4904-row English KV STUDIO device-comment CSV."
    )
    parser.add_argument("translations", type=Path, help="UTF-8 JSON source-to-English map")
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "comments-export" / "original-comments.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "comments-export" / "english-comments.csv",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=ROOT / "reports" / "13-device-comments-english-audit.json",
    )
    args = parser.parse_args(argv)

    paths = [args.input, args.translations, args.output, args.audit]
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if _same_path(path, other):
                parser.error(f"input, translations, output and audit paths must differ: {path}")

    source_before = _sha256(args.input.read_bytes())
    try:
        output_raw, audit = build(args.input, args.translations)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output_raw)
    source_after = _sha256(args.input.read_bytes())
    if source_after != source_before:
        raise RuntimeError("original CSV changed while building the English export")
    audit["source"]["sha256_after_build"] = source_after
    audit["source"]["immutable"] = True
    audit["output"]["path"] = str(args.output)
    args.audit.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
