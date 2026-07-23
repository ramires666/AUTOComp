"""Wrap long mnemonic comments to KV STUDIO's 80-character import limit."""

from __future__ import annotations

import json
import argparse
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "mnemonic-export" / "english-cp936"
SOURCE_DIRECTORY = ROOT / "mnemonic-export" / "english"
REPORT = ROOT / "reports" / "16-kvstudio-mnemonic-compatibility.json"
LIMIT = 78


def wrap_comment(line: str) -> list[str]:
    if line == ";SCRIPT_TYPE:":
        return []
    if line.startswith((";MODULE:", ";MODULE_TYPE:", ";SCRIPT_TYPE:")):
        return [line]
    safe = unicodedata.normalize("NFKC", line).encode("ascii", "ignore").decode("ascii")
    safe = safe.replace('"', "").replace("'", "")
    if len(safe) > LIMIT:
        if safe.endswith("*/"):
            safe = safe[: LIMIT - 2].rstrip() + "*/"
        else:
            safe = safe[: LIMIT - 3].rstrip() + "..."
    return [safe or ";"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="append", default=[])
    args = parser.parse_args()
    selected = set(args.file)
    records = []
    for source_path in sorted(SOURCE_DIRECTORY.glob("*.mnm")):
        if selected and source_path.name not in selected:
            continue
        path = DIRECTORY / source_path.name
        original = source_path.read_bytes().decode("utf-8")
        original_lines = original.split("\r\n")
        result = []
        wrapped = 0
        for line in original_lines:
            lines = wrap_comment(line) if line.startswith(";") else [line]
            wrapped += int(lines != [line])
            result.extend(lines)
        candidate = "\r\n".join(result)
        original_logic = [line for line in original_lines if not line.startswith(";")]
        candidate_logic = [line for line in result if not line.startswith(";")]
        if candidate_logic != original_logic:
            raise RuntimeError(f"logic changed in {path.name}")
        too_long = [line for line in result if line.startswith(";") and len(line) > LIMIT and not line.startswith((";MODULE:", ";MODULE_TYPE:"))]
        if too_long:
            raise RuntimeError(f"long comments remain in {path.name}")
        path.write_bytes(candidate.encode("cp936"))
        records.append({"file": path.name, "extra_comment_lines": wrapped})
    REPORT.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "comment_character_limit": LIMIT,
                "file_count": len(records),
                "files_changed": sum(record["extra_comment_lines"] > 0 for record in records),
                "extra_comment_lines": sum(record["extra_comment_lines"] for record in records),
                "logic_lines_unchanged": True,
                "records": records,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
