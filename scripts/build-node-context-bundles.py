"""Bundle each PLC program with mnemonic and referenced device-comment context."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROJECT = ROOT / "reports" / "10-new-project-full-mirror.json"
DEFAULT_MNEMONIC_INVENTORY = ROOT / "reports" / "11-english-mnemonic-inventory.json"
DEFAULT_MNEMONIC_TRANSLATIONS = ROOT / "reports" / "11-english-span-translations.json"
DEFAULT_DEVICE_INVENTORY = ROOT / ".autocomp" / "device-comment-inventory.json"
DEFAULT_OUTPUT = ROOT / "reports" / "14-node-context-bundles.json"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ADDRESS_RE = re.compile(r"([A-Za-z]+)(\d+)")
_QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_METADATA_PREFIXES = (";MODULE:", ";MODULE_TYPE:", ";SCRIPT_TYPE:")


class BundleError(ValueError):
    """An input artifact is incomplete or internally inconsistent."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_address(prefix: str, number: str) -> str:
    return f"{prefix.upper()}{int(number)}"


def _translation_map(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise BundleError("mnemonic translation artifact must be an object")
    source = payload.get("translations", payload)
    if not isinstance(source, dict):
        raise BundleError("mnemonic translations must be an object")
    translations: dict[str, str] = {}
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise BundleError("mnemonic translation keys and values must be strings")
        translations[key] = value
    return translations


def _english_lines(
    lines: list[dict[str, Any]], translations: dict[str, str]
) -> list[dict[str, Any]]:
    translated = copy.deepcopy(lines)
    missing: set[str] = set()
    for line in translated:
        text = line.get("text")
        if not isinstance(text, str):
            raise BundleError("mnemonic line text must be a string")
        stripped = text.lstrip()
        protected = bool(
            _CJK_RE.search(text)
            and (
                stripped.startswith(";MODULE:")
                or not stripped.startswith(";")
                or "DM1300.T" in text
            )
        )
        if protected:
            continue

        def replace(match: re.Match[str]) -> str:
            source = match.group()
            target = translations.get(source)
            if target is None:
                missing.add(source)
                return source
            return target

        line["text"] = _CJK_RE.sub(replace, text)
    if missing:
        raise BundleError(
            "mnemonic translation map is missing exact CJK spans: "
            + json.dumps(sorted(missing), ensure_ascii=False)
        )
    return translated


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
        or (body.startswith("-") and any(char.isalpha() for char in body))
    )


def _device_index(device_inventory: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    sources = device_inventory.get("sources")
    if not isinstance(sources, list):
        raise BundleError("device inventory has no sources array")
    index: dict[str, dict[str, Any]] = {}
    prefixes: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("source_text"), str):
            raise BundleError("invalid device inventory source")
        rows = source.get("rows")
        if not isinstance(rows, list):
            raise BundleError("device inventory source has no rows")
        for row in rows:
            if not isinstance(row, dict):
                raise BundleError("invalid device inventory row")
            address = row.get("address")
            canonical = row.get("canonical_address")
            if not isinstance(address, str) or not isinstance(canonical, str):
                raise BundleError("device inventory row lacks address identity")
            match = _ADDRESS_RE.fullmatch(address)
            if match is None:
                raise BundleError(f"unsupported device address: {address!r}")
            prefixes.add(match.group(1).upper())
            if canonical in index:
                raise BundleError(f"duplicate canonical device address: {canonical}")
            index[canonical] = {
                "address": address,
                "source_text": source["source_text"],
                "inventory_english": row.get("english", source.get("english")),
            }
    return index, prefixes


def _device_pattern(prefixes: set[str]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(prefix) for prefix in sorted(prefixes, key=lambda value: (-len(value), value))
    )
    return re.compile(
        rf"(?<![A-Za-z0-9_])(?P<indirect>@?)(?P<prefix>{alternatives})"
        rf"(?P<number>\d+)(?:\.(?P<suffix>[A-Za-z]+))?",
        re.IGNORECASE,
    )


def _load_device_translations(
    path: Path | None, known_addresses: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    by_address: dict[str, str] = {}
    by_source: dict[str, str] = {}
    if path is None:
        return by_address, by_source
    payload = _read_json(path)
    source = payload.get("translations", payload) if isinstance(payload, dict) else payload
    if isinstance(source, dict):
        entries = [{"key": key, "english": value} for key, value in source.items()]
    elif isinstance(source, list):
        entries = source
    else:
        raise BundleError("device translation map must be an object or array")

    for entry in entries:
        if not isinstance(entry, dict):
            raise BundleError("device translation entries must be objects")
        english = entry.get("english", entry.get("target"))
        if not isinstance(english, str):
            raise BundleError("device translation entry lacks string english/target")
        address = entry.get("address")
        source_text = entry.get("source", entry.get("source_text"))
        key = entry.get("key")
        if isinstance(key, str):
            match = _ADDRESS_RE.fullmatch(key)
            canonical = _canonical_address(*match.groups()) if match else ""
            if canonical in known_addresses:
                address = canonical
            else:
                source_text = key
        if isinstance(address, str):
            match = _ADDRESS_RE.fullmatch(address)
            if match is None:
                raise BundleError(f"unsupported translated device address: {address!r}")
            canonical = _canonical_address(*match.groups())
            previous = by_address.setdefault(canonical, english)
            if previous != english:
                raise BundleError(f"conflicting device translation for {address}")
        elif isinstance(source_text, str):
            previous = by_source.setdefault(source_text, english)
            if previous != english:
                raise BundleError(f"conflicting device translation for {source_text!r}")
        else:
            raise BundleError("device translation entry needs address or source text")
    return by_address, by_source


def _referenced_device_comments(
    lines: list[dict[str, Any]],
    device_index: dict[str, dict[str, Any]],
    pattern: re.Pattern[str],
    by_address: dict[str, str],
    by_source: dict[str, str],
) -> list[dict[str, Any]]:
    occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    nearest_heading: dict[str, Any] | None = None
    for line in lines:
        number = int(line.get("number", 0))
        text = line.get("text")
        if not isinstance(text, str):
            raise BundleError("mnemonic line text must be a string")
        if _is_heading(text):
            nearest_heading = {"line_number": number, "text": text}
        if text.lstrip().startswith(";"):
            continue
        for match in pattern.finditer(_QUOTED_RE.sub('""', text)):
            canonical = _canonical_address(match.group("prefix"), match.group("number"))
            if canonical not in device_index:
                continue
            if canonical not in occurrences:
                order.append(canonical)
            occurrences[canonical].append(
                {
                    "line_number": number,
                    "instruction": text,
                    "matched_token": match.group(0),
                    "indirect": bool(match.group("indirect")),
                    "suffix": match.group("suffix") or "",
                    "nearest_heading": nearest_heading,
                }
            )

    result: list[dict[str, Any]] = []
    for canonical in order:
        device = device_index[canonical]
        source_text = device["source_text"]
        english = by_address.get(
            canonical, by_source.get(source_text, device.get("inventory_english"))
        )
        result.append(
            {
                "address": device["address"],
                "canonical_address": canonical,
                "source_text": source_text,
                "english": english,
                "occurrence_count": len(occurrences[canonical]),
                "contexts": occurrences[canonical][:3],
            }
        )
    return result


def build_bundles(
    project: dict[str, Any],
    mnemonic_inventory: list[Any],
    mnemonic_translations: dict[str, str],
    device_inventory: dict[str, Any],
    *,
    device_translation_path: Path | None = None,
) -> dict[str, Any]:
    programs = project.get("programs")
    if not isinstance(programs, list) or len(programs) != 48:
        raise BundleError("project mirror must contain exactly 48 programs")
    if not isinstance(mnemonic_inventory, list):
        raise BundleError("mnemonic inventory must be an array")

    device_index, prefixes = _device_index(device_inventory)
    pattern = _device_pattern(prefixes)
    by_address, by_source = _load_device_translations(
        device_translation_path, set(device_index)
    )

    units_by_path: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for unit in mnemonic_inventory:
        if not isinstance(unit, dict) or not isinstance(unit.get("hierarchy"), list):
            raise BundleError("invalid mnemonic inventory record")
        units_by_path[tuple(str(part) for part in unit["hierarchy"])].append(unit)

    bundles: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, ...]] = set()
    for program in programs:
        if not isinstance(program, dict):
            raise BundleError("invalid program entry")
        tree_map = program.get("tree_map")
        content = program.get("content")
        if not isinstance(tree_map, dict) or not isinstance(content, dict):
            raise BundleError("program lacks tree_map/content")
        names = tree_map.get("names")
        paths = tree_map.get("paths")
        locator = tree_map.get("locator")
        original = content.get("original")
        if (
            not isinstance(names, dict)
            or not isinstance(paths, dict)
            or not isinstance(locator, list)
            or not isinstance(original, dict)
            or not isinstance(original.get("lines"), list)
        ):
            raise BundleError("program has incomplete tree or original mnemonic data")
        source_path = paths.get("original")
        english_path = paths.get("english")
        if not isinstance(source_path, list) or not isinstance(english_path, list):
            raise BundleError("program lacks source/English tree paths")
        path_key = tuple(str(part) for part in source_path)
        seen_paths.add(path_key)
        original_lines = copy.deepcopy(original["lines"])
        english_lines = _english_lines(original_lines, mnemonic_translations)
        bundles.append(
            {
                "locator": locator,
                "tree": {
                    "source_name": names.get("original"),
                    "english_name": names.get("english"),
                    "source_path": source_path,
                    "english_path": english_path,
                },
                "mnemonic": {
                    "source_file": original.get("file"),
                    "original_lines": original_lines,
                    "english_lines": english_lines,
                    "translation_units": copy.deepcopy(units_by_path.get(path_key, [])),
                },
                "device_comments": _referenced_device_comments(
                    original_lines,
                    device_index,
                    pattern,
                    by_address,
                    by_source,
                ),
            }
        )

    unmatched_units = sorted(" / ".join(path) for path in set(units_by_path) - seen_paths)
    if unmatched_units:
        raise BundleError(
            "mnemonic inventory contains unmatched program paths: "
            + json.dumps(unmatched_units, ensure_ascii=False)
        )
    return {
        "schema_version": 1,
        "artifact_type": "program_node_context_bundles",
        "summary": {
            "program_count": len(bundles),
            "original_line_count": sum(
                len(bundle["mnemonic"]["original_lines"]) for bundle in bundles
            ),
            "english_line_count": sum(
                len(bundle["mnemonic"]["english_lines"]) for bundle in bundles
            ),
            "translation_unit_count": len(mnemonic_inventory),
            "referenced_device_comment_count": sum(
                len(bundle["device_comments"]) for bundle in bundles
            ),
        },
        "bundles": bundles,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument(
        "--mnemonic-inventory", type=Path, default=DEFAULT_MNEMONIC_INVENTORY
    )
    parser.add_argument(
        "--mnemonic-translations", type=Path, default=DEFAULT_MNEMONIC_TRANSLATIONS
    )
    parser.add_argument("--device-inventory", type=Path, default=DEFAULT_DEVICE_INVENTORY)
    parser.add_argument("--device-translations", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = _read_json(args.project)
    mnemonic_inventory = _read_json(args.mnemonic_inventory)
    translations = _translation_map(_read_json(args.mnemonic_translations))
    device_inventory = _read_json(args.device_inventory)
    if not isinstance(project, dict) or not isinstance(device_inventory, dict):
        raise BundleError("project and device inventory roots must be objects")
    result = build_bundles(
        project,
        mnemonic_inventory,
        translations,
        device_inventory,
        device_translation_path=args.device_translations,
    )
    result["sources"] = {
        "project": {"path": args.project.as_posix(), "sha256": _sha256(args.project)},
        "mnemonic_inventory": {
            "path": args.mnemonic_inventory.as_posix(),
            "sha256": _sha256(args.mnemonic_inventory),
        },
        "mnemonic_translations": {
            "path": args.mnemonic_translations.as_posix(),
            "sha256": _sha256(args.mnemonic_translations),
        },
        "device_inventory": {
            "path": args.device_inventory.as_posix(),
            "sha256": _sha256(args.device_inventory),
        },
        "device_translations": None
        if args.device_translations is None
        else {
            "path": args.device_translations.as_posix(),
            "sha256": _sha256(args.device_translations),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
