"""Build one strict KV STUDIO device-comment CSV for each program module."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / ".autocomp" / "local-device-comment-inventory.json"
DEFAULT_TRANSLATIONS = ROOT / ".autocomp" / "local-device-comment-translations.json"
DEFAULT_TREE = ROOT / ".autocomp" / "tree-zero-cjk-after-fragments.json"
DEFAULT_OUTPUT = ROOT / "comments-export" / "local-english"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def current_programs(tree: dict[str, Any]) -> list[str]:
    roots = tree["project_tree_inventory"]["roots"]
    program_root = next(root for root in roots if root.get("locator") == [4])
    every_scan = next(
        child for child in program_root["children"] if child.get("locator") == [4, 0]
    )
    programs = [str(child["name"]) for child in every_scan["children"]]
    if len(programs) != 47:
        raise ValueError(f"expected 47 every-scan programs, found {len(programs)}")
    return programs


def module_index(raw_index: int) -> int:
    # The translated replacement of original program 1 was appended after the
    # other 46 every-scan programs.  KV's module combo follows this current order:
    # Global=0, Main_EN=1, ..., GripperCylinderEndurance=46, Init...=47.
    if raw_index == 1:
        return 47
    if 2 <= raw_index <= 47:
        return raw_index - 1
    raise ValueError(f"unsupported local-comment program raw index: {raw_index}")


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return value[:80] or "program"


def translations_map(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("translations", payload)
    if not isinstance(raw, dict):
        raise ValueError("translation artifact has no translations object")
    result: dict[str, str] = {}
    for source, target in raw.items():
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        target = target.strip()
        if (
            not target
            or not target.isascii()
            or len(target.encode("ascii")) > 32
            or any(char in target for char in "\r\n,")
        ):
            raise ValueError(f"invalid local comment translation for {source!r}: {target!r}")
        result[source] = target
    return result


def encode_rows(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerows(rows)
    text = buffer.getvalue()
    if '"' in text:
        raise ValueError("generated CSV unexpectedly requires quoting")
    return text.encode("cp936")


def build(
    inventory_path: Path,
    translations_path: Path,
    tree_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    inventory = read_json(inventory_path)
    entries = inventory["entries"]
    translations = translations_map(read_json(translations_path))
    programs = current_programs(read_json(tree_path))

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    missing: set[str] = set()
    for entry in entries:
        source = str(entry["source_text"])
        if source not in translations:
            missing.add(source)
        grouped[int(entry["program_raw_index"])].append(entry)
    if missing:
        raise ValueError(f"{len(missing)} local comments are untranslated")
    if len(grouped) != 35:
        raise ValueError(f"expected 35 local-comment programs, found {len(grouped)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    modules: list[dict[str, Any]] = []
    bilingual_entries: list[dict[str, Any]] = []
    total_rows = 0
    for raw_index in sorted(grouped):
        combo_index = module_index(raw_index)
        program_name = programs[combo_index - 1]
        program_entries = grouped[raw_index]
        rows = [
            [str(entry["address"]), "", translations[str(entry["source_text"])], "", ""]
            for entry in program_entries
        ]
        filename = (
            f"{combo_index:03d}-src{raw_index:03d}-"
            f"{safe_name(program_name)}.csv"
        )
        raw = encode_rows(rows)
        path = output_dir / filename
        path.write_bytes(raw)
        reparsed = list(csv.reader(io.StringIO(raw.decode("cp936"), newline="")))
        if reparsed != rows:
            raise AssertionError(f"CSV round-trip mismatch: {filename}")
        modules.append(
            {
                "module_index": combo_index,
                "source_raw_index": raw_index,
                "program_name": program_name,
                "filename": filename,
                "row_count": len(rows),
            }
        )
        bilingual_entries.extend(
            {
                "module_index": combo_index,
                "source_raw_index": raw_index,
                "program_name": program_name,
                "address": str(entry["address"]),
                "source_zh": str(entry["source_text"]),
                "target_en": translations[str(entry["source_text"])],
            }
            for entry in program_entries
        )
        total_rows += len(rows)

    modules.sort(key=lambda item: int(item["module_index"]))
    bilingual_entries.sort(
        key=lambda item: (
            int(item["module_index"]),
            str(item["address"]),
            str(item["source_zh"]),
        )
    )
    bilingual = {
        "schema_version": 1,
        "artifact_type": "kvstudio_local_device_comments_bilingual",
        "entry_count": len(bilingual_entries),
        "unique_source_count": len(translations),
        "translations": dict(sorted(translations.items())),
        "entries": bilingual_entries,
    }
    bilingual_name = "bilingual.json"
    (output_dir / bilingual_name).write_text(
        json.dumps(bilingual, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "artifact_type": "kvstudio_local_device_comment_csv_manifest",
        "selection": "Global=0; module_index is the current combo position",
        "encoding": "CP936",
        "column_count": 5,
        "module_count": len(modules),
        "row_count": total_rows,
        "bilingual_file": bilingual_name,
        "modules": modules,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--translations", type=Path, default=DEFAULT_TRANSLATIONS)
    parser.add_argument("--tree", type=Path, default=DEFAULT_TREE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = build(
        args.inventory.resolve(),
        args.translations.resolve(),
        args.tree.resolve(),
        args.output_dir.resolve(),
    )
    print(
        f"built {manifest['module_count']} module CSVs, "
        f"{manifest['row_count']} comments"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
