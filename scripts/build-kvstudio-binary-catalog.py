"""Join the bilingual project tree with lossless KV STUDIO binary captures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _locator(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"invalid locator: {value!r}")
    return tuple(int(part) for part in value)


def _index_unique(
    records: list[dict[str, Any]], label: str
) -> dict[tuple[int, ...], dict[str, Any]]:
    result: dict[tuple[int, ...], dict[str, Any]] = {}
    for record in records:
        key = _locator(record.get("locator"))
        if key in result:
            raise ValueError(f"duplicate {label} locator: {list(key)}")
        result[key] = record
    return result


def _decoded_records(parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter[str], int]:
    decoded: list[dict[str, Any]] = []
    cjk = Counter[str]()
    cjk_occurrences = 0
    for section_index, section in enumerate(parsed.get("sections", [])):
        for group_index, group in enumerate(section.get("groups", [])):
            for record_index, record in enumerate(group.get("records", [])):
                text = record.get("text")
                if not isinstance(text, str):
                    continue
                matches = list(CJK_RE.finditer(text))
                for match in matches:
                    cjk[match.group()] += 1
                cjk_occurrences += len(matches)
                decoded.append(
                    {
                        "section_index": section_index,
                        "section_tag": section.get("tag"),
                        "group_index": group_index,
                        "record_index": record_index,
                        "record_offset": record.get("offset"),
                        "subtype": record.get("subtype"),
                        "subtype_hex": record.get("subtype_hex"),
                        "index": record.get("index"),
                        "text": text,
                        "text_encoding": record.get("text_encoding"),
                        "text_offset": record.get("text_offset"),
                        "text_end_offset": record.get("text_end_offset"),
                        "raw_text_base64": record.get("raw_text_base64"),
                        "raw_text_region_base64": record.get("raw_text_region_base64"),
                        "cjk_occurrences": [
                            {"text": match.group(), "start": match.start(), "end": match.end()}
                            for match in matches
                        ],
                    }
                )
    return decoded, cjk, cjk_occurrences


def build(tree_path: Path, state_path: Path, parsed_dir: Path) -> dict[str, Any]:
    tree = _load(tree_path)
    state = _load(state_path)
    current_tree_path = state_path.parent / "tree-inventory.raw.json"
    current_tree = _load(current_tree_path)
    tree_records = tree.get("records")
    state_programs = state.get("programs")
    current_tree_inventory = current_tree.get("project_tree_inventory")
    if not isinstance(tree_records, list) or not all(
        isinstance(item, dict) for item in tree_records
    ):
        raise ValueError("tree catalog has no object records array")
    if not isinstance(state_programs, dict) or not all(
        isinstance(item, dict) for item in state_programs.values()
    ):
        raise ValueError("capture state has no programs object")
    if (
        not isinstance(current_tree_inventory, dict)
        or current_tree_inventory.get("complete") is not True
        or current_tree_inventory.get("item_count") != len(tree_records)
    ):
        raise ValueError("current tree capture is incomplete or does not match tree catalog")

    tree_by_locator = _index_unique(tree_records, "tree")
    programs = sorted(state_programs.values(), key=lambda item: _locator(item.get("locator")))
    global_cjk = Counter[str]()
    total_cjk_occurrences = 0
    total_decoded = 0
    missing: list[dict[str, Any]] = []
    output_programs: list[dict[str, Any]] = []

    for state_record in programs:
        locator = _locator(state_record.get("locator"))
        tree_record = tree_by_locator.get(locator)
        attempts = state_record.get("attempts")
        selected = state_record.get("selected_attempt")
        if tree_record is None:
            missing.append({"locator": list(locator), "component": "tree_record"})
            continue
        if (
            not isinstance(attempts, list)
            or not isinstance(selected, int)
            or not 0 <= selected < len(attempts)
        ):
            missing.append({"locator": list(locator), "component": "selected_capture_attempt"})
            continue
        attempt = attempts[selected]
        if not isinstance(attempt, dict) or not isinstance(attempt.get("binary_file"), str):
            missing.append({"locator": list(locator), "component": "binary_capture"})
            continue
        binary_path = state_path.parent / attempt["binary_file"]
        parsed_path = parsed_dir / f"{binary_path.name}.parsed.json"
        if not binary_path.is_file():
            missing.append(
                {"locator": list(locator), "component": "binary_file", "path": str(binary_path)}
            )
            continue
        if not parsed_path.is_file():
            missing.append(
                {"locator": list(locator), "component": "parsed_file", "path": str(parsed_path)}
            )
            continue
        binary_sha256 = _sha256(binary_path)
        parsed = _load(parsed_path)
        if binary_sha256 != attempt.get("sha256") or binary_sha256 != parsed.get("sha256"):
            raise ValueError(f"binary SHA-256 mismatch at locator {list(locator)}")
        if parsed.get("source_file") != binary_path.name:
            raise ValueError(f"parsed source filename mismatch at locator {list(locator)}")

        decoded, program_cjk, occurrence_count = _decoded_records(parsed)
        russian_path = [
            tree_by_locator[locator[:depth]].get("russian_name", "")
            for depth in range(1, len(locator) + 1)
        ]
        global_cjk.update(program_cjk)
        total_cjk_occurrences += occurrence_count
        total_decoded += len(decoded)
        output_programs.append(
            {
                "locator": list(locator),
                "tree_multilingual": {
                    "original": {
                        "name": tree_record.get("original_name"),
                        "path": tree_record.get("original_path"),
                    },
                    "english": {
                        "name": tree_record.get("english_name"),
                        "path": tree_record.get("english_path"),
                    },
                    "russian": {
                        "name": tree_record.get("russian_name", ""),
                        "path": russian_path,
                    },
                    "current_name": tree_record.get("current_tree_name"),
                    "english_status": tree_record.get("english_status"),
                    "translation_record_id": tree_record.get("translation_record_id"),
                    "exact_tree_record": tree_record,
                },
                "source_capture": {
                    "state_record": state_record,
                    "selected_attempt": attempt,
                    "binary_path": binary_path.relative_to(ROOT).as_posix(),
                    "parsed_path": parsed_path.relative_to(ROOT).as_posix(),
                    "binary_byte_length": binary_path.stat().st_size,
                    "binary_base64": base64.b64encode(binary_path.read_bytes()).decode(
                        "ascii"
                    ),
                    "verified_binary_sha256": binary_sha256,
                },
                "parsed_binary": parsed,
                "decoded_text_records": decoded,
                "cjk": {
                    "occurrence_count": occurrence_count,
                    "unique_string_count": len(program_cjk),
                    "unique_strings": [
                        {"text": text, "occurrences": count}
                        for text, count in sorted(program_cjk.items())
                    ],
                },
            }
        )

    if missing:
        raise ValueError(
            f"catalog inputs are incomplete: {json.dumps(missing, ensure_ascii=False)}"
        )
    if len(output_programs) != 48:
        raise ValueError(f"expected 48 program locators, found {len(output_programs)}")

    return {
        "schema_version": 1,
        "artifact_type": "kvstudio_full_project_bilingual_binary_catalog",
        "inputs": {
            "tree_catalog": {
                "path": tree_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(tree_path),
            },
            "capture_state": {
                "path": state_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(state_path),
            },
            "current_tree_capture": {
                "path": current_tree_path.relative_to(ROOT).as_posix(),
                "sha256": _sha256(current_tree_path),
            },
            "parsed_directory": parsed_dir.relative_to(ROOT).as_posix(),
        },
        "capture_provenance": {
            "checkpoint": state.get("checkpoint"),
            "worker": state.get("worker"),
            "inventory": state.get("inventory"),
        },
        "summary": {
            "tree_node_count": len(tree_records),
            "program_count": len(output_programs),
            "decoded_text_record_count": total_decoded,
            "cjk_occurrence_count": total_cjk_occurrences,
            "cjk_unique_string_count": len(global_cjk),
            "missing_count": 0,
            "missing": [],
        },
        "cjk": {
            "occurrence_count": total_cjk_occurrences,
            "unique_string_count": len(global_cjk),
            "unique_strings": [
                {"text": text, "occurrences": count} for text, count in sorted(global_cjk.items())
            ],
        },
        "project_tree": tree,
        "current_project_tree_capture": current_tree,
        "programs": output_programs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree", type=Path, default=ROOT / "reports/04-full-tree-bilingual-catalog.json"
    )
    parser.add_argument(
        "--state", type=Path, default=ROOT / ".autocomp/kvstudio-raw-all/state.json"
    )
    parser.add_argument(
        "--parsed-dir", type=Path, default=ROOT / ".autocomp/kvstudio-raw-all/parsed"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/07-full-project-bilingual.json"
    )
    args = parser.parse_args(argv)
    result = build(args.tree.resolve(), args.state.resolve(), args.parsed_dir.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
