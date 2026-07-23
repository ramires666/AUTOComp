"""Translate inventoried local KV device comments without touching the GUI.

Exact full-source matches are reused from the reviewed translation artifacts.
Only the remaining comments are sent to the configured OpenAI-compatible LLM.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BASE_SCRIPT = ROOT / "scripts" / "translate-device-comments-resumable.py"
DEFAULT_INVENTORY = ROOT / ".autocomp" / "local-device-comment-inventory.json"
DEFAULT_OUTPUT = ROOT / ".autocomp" / "local-device-comment-translations.json"
DEFAULT_SEED_OUTPUT = ROOT / ".autocomp" / "local-device-comment-seeds.json"
DEFAULT_ENRICHED = ROOT / ".autocomp" / "local-device-comment-inventory-seeded.json"
DEFAULT_SEED_MAPS = (
    ROOT / ".autocomp" / "device-comment-translations-final.json",
    ROOT / "reports" / "11-english-span-translations.json",
)


def _load_base() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "autocomp_translate_device_comments_resumable", BASE_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_base()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def _translation_map(path: Path) -> dict[str, str]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"translation map root must be an object: {path}")
    raw = payload.get("translations", payload)
    if not isinstance(raw, dict):
        raise ValueError(f"translations must be an object: {path}")
    return {
        source: target.strip()
        for source, target in raw.items()
        if isinstance(source, str)
        and isinstance(target, str)
        and BASE._valid_target(target.strip())
    }


def _inventory_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("local device-comment inventory must be an object or array")
    for key in ("items", "sources", "comments", "entries"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise ValueError("local device-comment inventory has no items, sources, or comments array")


def _normalize_inventory(payload: Any) -> list[dict[str, Any]]:
    """Accept the final inventory and a few simple one-off inventory shapes."""
    normalized: list[dict[str, Any]] = []
    by_source: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_inventory_items(payload)):
        if isinstance(raw, str):
            item: dict[str, Any] = {"source": raw}
        elif isinstance(raw, dict):
            item = dict(raw)
        else:
            raise ValueError(f"inventory item {index} must be an object or string")

        source = next(
            (
                item.get(key)
                for key in ("source", "source_text", "comment", "text")
                if isinstance(item.get(key), str) and item[key].strip()
            ),
            None,
        )
        if not isinstance(source, str):
            raise ValueError(f"inventory item {index} has no source text")
        location = {
            key: item[key]
            for key in (
                "program_raw_index",
                "program_name",
                "program_locator",
                "address",
                "source_file",
            )
            if key in item
        }
        if source in by_source:
            by_source[source]["rows"].append(location or {"index": index})
            by_source[source]["context_samples"].append(location)
            continue
        normalized_item = {
            "id": hashlib.sha256(source.encode("utf-8")).hexdigest()[:20],
            "source": source,
            "rows": [location or {"index": index}],
            "context_samples": [location],
        }
        by_source[source] = normalized_item
        normalized.append(normalized_item)
    return normalized


def _seed_items(
    items: list[dict[str, Any]], seed_paths: tuple[Path, ...]
) -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    seed_maps = [(path, _translation_map(path)) for path in seed_paths]
    selected: dict[str, str] = {}
    origins: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []

    for item in items:
        source = str(item["source"])
        matches = [
            (path, translations[source])
            for path, translations in seed_maps
            if source in translations
        ]
        if matches:
            # First map is the reviewed device-comment artifact and is authoritative.
            chosen_path, chosen_target = matches[0]
            for other_path, other_target in matches[1:]:
                if other_target != chosen_target:
                    conflicts.append(
                        {
                            "source": source,
                            "chosen": chosen_target,
                            "chosen_from": chosen_path.as_posix(),
                            "ignored": other_target,
                            "ignored_from": other_path.as_posix(),
                        }
                    )
            item["reuse_target"] = chosen_target
            selected[source] = chosen_target
            origins[source] = chosen_path.as_posix()
            continue

        existing = item.get("reuse_target")
        if isinstance(existing, str) and BASE._valid_target(existing.strip()):
            item["reuse_target"] = existing.strip()

    return selected, origins, conflicts


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run(
    *,
    inventory_path: Path,
    output_path: Path,
    seed_output_path: Path,
    enriched_inventory_path: Path,
    seed_paths: tuple[Path, ...] = DEFAULT_SEED_MAPS,
    batch_size: int = 100,
    max_batches: int = 10_000,
    config_path: Path = ROOT / "config.local.json",
    env_path: Path = ROOT / ".env",
) -> dict[str, Any]:
    items = _normalize_inventory(_read_json(inventory_path))
    seeds, origins, conflicts = _seed_items(items, seed_paths)

    seed_payload = {
        "schema_version": 1,
        "artifact_type": "local_device_comment_seed_translations",
        "matching": "exact_full_source",
        "constraints": {"encoding": "ASCII", "maximum_characters": 32},
        "source_maps": [path.as_posix() for path in seed_paths],
        "translations": seeds,
        "origins": origins,
        "conflicts": conflicts,
        "progress": {
            "inventory_total": len(items),
            "seeded": len(seeds),
            "remaining_for_llm": len(items) - len(seeds),
        },
    }
    _write_json(seed_output_path, seed_payload)
    _write_json(
        enriched_inventory_path,
        {
            "schema_version": 1,
            "source_inventory": inventory_path.as_posix(),
            "items": items,
        },
    )

    empty_glossary = enriched_inventory_path.with_name(
        enriched_inventory_path.stem + "-empty-glossary.json"
    )
    _write_json(empty_glossary, {})
    return BASE.run(
        inventory_path=enriched_inventory_path,
        output_path=output_path,
        glossary_path=empty_glossary,
        batch_size=batch_size,
        max_batches=max_batches,
        config_path=config_path,
        env_path=env_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed-output", type=Path, default=DEFAULT_SEED_OUTPUT)
    parser.add_argument("--enriched-inventory", type=Path, default=DEFAULT_ENRICHED)
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.json")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=10_000)
    args = parser.parse_args(argv)

    state = run(
        inventory_path=args.inventory.resolve(),
        output_path=args.output.resolve(),
        seed_output_path=args.seed_output.resolve(),
        enriched_inventory_path=args.enriched_inventory.resolve(),
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        config_path=args.config.resolve(),
        env_path=args.env_file.resolve(),
    )
    print(json.dumps(state["progress"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
