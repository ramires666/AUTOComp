"""Extract per-program local @DM/@MR comments from parsed KV STUDIO captures."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PARSED_DIR = ROOT / ".autocomp" / "kvstudio-raw-all" / "parsed"
DEFAULT_STATE = ROOT / ".autocomp" / "kvstudio-raw-all" / "state.json"
DEFAULT_OUTPUT = ROOT / ".autocomp" / "local-device-comment-inventory.json"
ADDRESS_PREFIX = {0x81: "@DM", 0x85: "@MR"}
EXPECTED = {"entry_count": 618, "unique_source_text_count": 550, "program_count": 35}


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def build(parsed_dir: Path, state_path: Path) -> dict[str, Any]:
    state = _load_object(state_path)
    programs = state.get("programs")
    if not isinstance(programs, dict):
        raise ValueError(f"state has no programs object: {state_path}")

    by_binary: dict[str, dict[str, Any]] = {}
    for program in programs.values():
        if not isinstance(program, dict):
            continue
        attempts = program.get("attempts")
        selected = program.get("selected_attempt")
        if not isinstance(attempts, list) or not isinstance(selected, int):
            continue
        if not 0 <= selected < len(attempts) or not isinstance(attempts[selected], dict):
            continue
        binary_file = attempts[selected].get("binary_file")
        if isinstance(binary_file, str):
            by_binary[Path(binary_file).name] = program

    entries: list[dict[str, Any]] = []
    for parsed_path in sorted(parsed_dir.glob("*.parsed.json")):
        parsed = _load_object(parsed_path)
        binary_name = parsed.get("source_file")
        if not isinstance(binary_name, str):
            raise ValueError(f"missing source_file: {parsed_path}")
        program = by_binary.get(binary_name)
        if program is None:
            raise ValueError(f"no program state for {binary_name}")
        raw_match = re.match(r"^(\d+)-", binary_name)
        if raw_match is None:
            raise ValueError(f"cannot read program raw index from {binary_name}")
        raw_index_text = raw_match.group(1)
        program_name = program.get("name")
        locator = program.get("locator")
        if not isinstance(program_name, str) or not isinstance(locator, list):
            raise ValueError(f"incomplete program state for {binary_name}")

        for section_index, section in enumerate(parsed.get("sections", [])):
            for group_index, group in enumerate(section.get("groups", [])):
                subtype = group.get("subtype")
                if subtype not in ADDRESS_PREFIX:
                    continue
                for record_index, record in enumerate(group.get("records", [])):
                    index = record.get("index")
                    source_text = record.get("text")
                    if not isinstance(index, int) or not isinstance(source_text, str):
                        raise ValueError(
                            f"invalid local comment record in {parsed_path} "
                            f"section {section_index}, group {group_index}, record {record_index}"
                        )
                    entries.append(
                        {
                            "program_raw_index": int(raw_index_text),
                            "program_raw_index_text": raw_index_text,
                            "program_name": program_name,
                            "program_locator": locator,
                            "subtype": subtype,
                            "subtype_hex": f"0x{subtype:02X}",
                            "index": index,
                            "address": f"{ADDRESS_PREFIX[subtype]}{index}",
                            "source_text": source_text,
                            "source_file": _relative(parsed_path),
                            "source_binary_file": binary_name,
                            "section_index": section_index,
                            "group_index": group_index,
                            "record_index": record_index,
                        }
                    )

    subtype_counts = Counter(entry["subtype_hex"] for entry in entries)
    summary = {
        "entry_count": len(entries),
        "unique_source_text_count": len({entry["source_text"] for entry in entries}),
        "program_count": len(
            {(entry["program_raw_index"], entry["program_name"]) for entry in entries}
        ),
        "by_subtype": dict(sorted(subtype_counts.items())),
    }
    for key, expected in EXPECTED.items():
        if summary[key] != expected:
            raise ValueError(f"expected {key}={expected}, found {summary[key]}")

    return {
        "schema_version": 1,
        "artifact_type": "kvstudio_local_device_comment_inventory",
        "inputs": {
            "parsed_directory": _relative(parsed_dir),
            "capture_state": _relative(state_path),
        },
        "address_mapping": {"0x81": "@DM", "0x85": "@MR"},
        "summary": summary,
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    result = build(args.parsed_dir, args.state)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = result["summary"]
    print(
        f"{args.output}: {summary['entry_count']} entries, "
        f"{summary['unique_source_text_count']} unique, "
        f"{summary['program_count']} programs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
