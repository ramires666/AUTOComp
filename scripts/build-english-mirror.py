"""Build a separate English mirror by replacing exact CJK spans offline."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _non_cjk_skeleton(value: str) -> list[str]:
    parts: list[str] = []
    cursor = 0
    for match in CJK_RE.finditer(value):
        parts.append(value[cursor : match.start()])
        cursor = match.end()
    parts.append(value[cursor:])
    return parts


def _translation_map(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    payload = _load_object(path)
    source = payload.get("translations", payload)
    translations: dict[str, str] = {}
    if isinstance(source, dict):
        for key, value in source.items():
            if key in {"allow_remaining", "allowed_remaining"} and source is payload:
                continue
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("translation map values must be strings")
            translations[key] = value
    elif isinstance(source, list):
        for item in source:
            if not isinstance(item, dict):
                raise ValueError("translation list entries must be objects")
            key = item.get("source")
            value = item.get("english", item.get("target"))
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("translation entries require string source and english/target")
            if key in translations and translations[key] != value:
                raise ValueError(f"conflicting translation for {key!r}")
            translations[key] = value
    else:
        raise ValueError("translations must be an object or array")

    allowed: dict[str, str] = {}
    allow_source = payload.get("allow_remaining", payload.get("allowed_remaining", []))
    if not isinstance(allow_source, list):
        raise ValueError("allow_remaining must be an array")
    for item in allow_source:
        if isinstance(item, str):
            allowed[item] = "explicitly allowed by translation map"
        elif isinstance(item, dict) and isinstance(item.get("source"), str):
            allowed[item["source"]] = str(item.get("reason", "explicitly allowed"))
        else:
            raise ValueError("allow_remaining entries must be strings or source/reason objects")

    overlap = sorted(set(translations).intersection(allowed))
    if overlap:
        raise ValueError(f"spans cannot be translated and allowed: {overlap}")
    for key, value in translations.items():
        if CJK_RE.fullmatch(key) is None:
            raise ValueError(f"translation source is not one exact CJK span: {key!r}")
        if not value or CJK_RE.search(value):
            raise ValueError(f"English translation is empty or still contains CJK: {key!r}")
    for key in allowed:
        if CJK_RE.fullmatch(key) is None:
            raise ValueError(f"allowed source is not one exact CJK span: {key!r}")
    return translations, allowed


class Translator:
    def __init__(self, translations: dict[str, str], allowed: dict[str, str]) -> None:
        self.translations = translations
        self.allowed = allowed
        self.missing: set[str] = set()
        self.remaining: list[dict[str, Any]] = []
        self.replacement_count = 0

    def text(self, value: str, *, location: str) -> str:
        pieces: list[str] = []
        cursor = 0
        for match in CJK_RE.finditer(value):
            pieces.append(value[cursor : match.start()])
            source = match.group()
            if source in self.translations:
                pieces.append(self.translations[source])
                self.replacement_count += 1
            elif source in self.allowed:
                pieces.append(source)
                self.remaining.append(
                    {
                        "location": location,
                        "source": source,
                        "reason": self.allowed[source],
                    }
                )
            else:
                pieces.append(source)
                self.missing.add(source)
            cursor = match.end()
        pieces.append(value[cursor:])
        return "".join(pieces)

    def preserve(self, value: str, *, location: str, reason: str) -> str:
        for match in CJK_RE.finditer(value):
            self.remaining.append(
                {
                    "location": location,
                    "source": match.group(),
                    "reason": reason,
                }
            )
        return value


def _translated_content(
    original: dict[str, Any],
    translator: Translator,
    *,
    program_index: int,
    locator: list[int],
    translations_sha256: str,
) -> tuple[dict[str, Any], str]:
    raw_text = original.get("raw_text")
    lines = original.get("lines")
    commands = original.get("commands")
    if (
        not isinstance(raw_text, str)
        or not isinstance(lines, list)
        or not isinstance(commands, list)
    ):
        raise ValueError(f"program {locator} has incomplete original content")
    prefix = f"programs[{'.'.join(str(part) for part in locator)}]"
    english_lines = copy.deepcopy(lines)
    protected_reasons: dict[int, str] = {}
    for line in english_lines:
        if not isinstance(line, dict) or not isinstance(line.get("text"), str):
            raise ValueError(f"program {locator} has invalid line metadata")
        number = int(line.get("number", 0))
        text = line["text"]
        stripped = text.lstrip()
        reason = ""
        if CJK_RE.search(text):
            if stripped.startswith(";MODULE:"):
                reason = "module identity pending Global rename"
            elif not stripped.startswith(";") or "DM1300.T" in text:
                reason = "runtime Mandarin voice"
        location = f"{prefix}.lines[{number}]"
        if reason:
            line["text"] = translator.preserve(text, location=location, reason=reason)
            protected_reasons[number] = reason
        else:
            line["text"] = translator.text(text, location=location)
    reconstructed = "".join(
        str(line["text"])
        + ({"CRLF": "\r\n", "LF": "\n", "CR": "\r", "": ""}.get(str(line.get("eol", "")), ""))
        for line in english_lines
    )
    english_text = reconstructed

    english_commands = copy.deepcopy(commands)
    line_text = {int(line["number"]): str(line["text"]) for line in english_lines}
    for command in english_commands:
        if not isinstance(command, dict) or not isinstance(command.get("text"), str):
            raise ValueError(f"program {locator} has invalid command metadata")
        number = int(command.get("line", 0))
        if number not in line_text:
            raise ValueError(f"program {locator} command refers to missing line {number}")
        command["text"] = line_text[number]
    if [command.get("opcode") for command in english_commands] != [
        command.get("opcode") for command in commands
    ]:
        raise ValueError(f"opcode order changed for program {locator}")

    encoded = english_text.encode("utf-8")
    filename = f"{program_index:03d}-{'_'.join(str(part) for part in locator)}.mnm"
    remaining = [
        {
            "line": number,
            "column": match.start() + 1,
            "text": match.group(),
            "reason": protected_reasons.get(
                number, translator.allowed.get(match.group(), "explicitly allowed")
            ),
        }
        for number, line in enumerate(english_text.splitlines(), start=1)
        for match in CJK_RE.finditer(line)
    ]
    content = {
        "file": filename,
        "capture": {
            "method": "exact_cjk_span_translation",
            "source": original.get("file"),
            "translations_sha256": translations_sha256,
        },
        "raw_bytes_sha256": hashlib.sha256(encoded).hexdigest(),
        "raw_bytes_size": len(encoded),
        "raw_bytes_base64": base64.b64encode(encoded).decode("ascii"),
        "encoding": "utf-8",
        "raw_text": english_text,
        "newline_styles": copy.deepcopy(original.get("newline_styles", [])),
        "terminal_newline": original.get("terminal_newline"),
        "lines": english_lines,
        "commands": english_commands,
        "cjk_occurrences": remaining,
        "non_cjk_skeleton_sha256": _canonical_hash(_non_cjk_skeleton(raw_text)),
    }
    return content, filename


def build(
    source: dict[str, Any], translations_path: Path
) -> tuple[dict[str, Any], dict[str, bytes]]:
    translations, allowed = _translation_map(translations_path)
    translator = Translator(translations, allowed)
    result = copy.deepcopy(source)
    programs = result.get("programs")
    if not isinstance(programs, list) or len(programs) != 48:
        raise ValueError("source mirror must contain exactly 48 programs")
    if source.get("missing_program_locators") or source.get("unmatched_exports"):
        raise ValueError("source mirror contains missing program mappings or unmatched exports")

    original_slots_before = _canonical_hash(
        [program.get("content", {}).get("original") for program in source["programs"]]
    )
    reviewed_tree_before = _canonical_hash(
        {
            "tree": source.get("tree"),
            "tree_hierarchy": source.get("tree_hierarchy"),
            "program_tree_maps": [program.get("tree_map") for program in source["programs"]],
        }
    )
    files: dict[str, bytes] = {}
    translations_sha256 = hashlib.sha256(translations_path.read_bytes()).hexdigest()
    seen_locators: set[tuple[int, ...]] = set()
    for index, program in enumerate(programs, start=1):
        tree_map = program.get("tree_map")
        content = program.get("content")
        if not isinstance(tree_map, dict) or not isinstance(content, dict):
            raise ValueError(f"program {index} lacks tree_map/content")
        locator_value = tree_map.get("locator")
        if not isinstance(locator_value, list):
            raise ValueError(f"program {index} has invalid locator")
        locator = tuple(int(part) for part in locator_value)
        if locator in seen_locators:
            raise ValueError(f"duplicate program locator: {list(locator)}")
        seen_locators.add(locator)
        original = content.get("original")
        if not isinstance(original, dict):
            raise ValueError(f"program {list(locator)} lacks original slot")
        english, filename = _translated_content(
            original,
            translator,
            program_index=index,
            locator=list(locator),
            translations_sha256=translations_sha256,
        )
        content["english"] = english
        files[filename] = english["raw_text"].encode("utf-8")

    if translator.missing:
        raise ValueError(
            "translation map is missing exact CJK spans: "
            + json.dumps(sorted(translator.missing), ensure_ascii=False)
        )
    original_slots_after = _canonical_hash(
        [program.get("content", {}).get("original") for program in result["programs"]]
    )
    if original_slots_before != original_slots_after:
        raise ValueError("original content slots changed")
    reviewed_tree_after = _canonical_hash(
        {
            "tree": result.get("tree"),
            "tree_hierarchy": result.get("tree_hierarchy"),
            "program_tree_maps": [program.get("tree_map") for program in result["programs"]],
        }
    )
    if reviewed_tree_before != reviewed_tree_after:
        raise ValueError("reviewed tree/program English fields changed")
    remaining = translator.remaining
    result.update(
        artifact_type="full_project_english_mirror",
        content_slot="english",
        complete=True,
        translation_source={
            "path": translations_path.as_posix(),
            "sha256": translations_sha256,
            "exact_span_count": len(translations),
        },
        english_mirror={
            "program_count": len(programs),
            "file_count": len(files),
            "replacement_count": translator.replacement_count,
            "missing_translation_count": 0,
            "remaining_cjk_count": len(remaining),
            "allowed_remaining": translator.remaining,
            "original_content_slots_sha256": original_slots_after,
            "reviewed_tree_maps_sha256": reviewed_tree_after,
        },
    )
    return result, files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("translations", type=Path)
    parser.add_argument(
        "--input", type=Path, default=ROOT / "reports/10-new-project-full-mirror.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/12-new-project-english-mirror.json"
    )
    parser.add_argument("--mnemonic-dir", type=Path, default=ROOT / "mnemonic-export/english")
    args = parser.parse_args(argv)
    result, files = build(_load_object(args.input), args.translations.resolve())
    args.mnemonic_dir.mkdir(parents=True, exist_ok=True)
    unexpected = sorted(
        path.name for path in args.mnemonic_dir.glob("*.mnm") if path.name not in files
    )
    if unexpected:
        raise ValueError(f"mnemonic output directory contains unexpected files: {unexpected}")
    for name, data in files.items():
        (args.mnemonic_dir / name).write_bytes(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["english_mirror"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
