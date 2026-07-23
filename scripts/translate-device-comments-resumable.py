"""Resume-safe local translation of KV device comments into short ASCII English."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autocomp.config import load_config  # noqa: E402
from autocomp.translation.client import (  # noqa: E402
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    TranslationProvider,
)
from autocomp.translation.models import ProviderBatchItem  # noqa: E402

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
DEFAULT_BATCH_SIZE = 50
MAX_COMMENT_LENGTH = 32
FORBIDDEN = frozenset({",", "'", '"', "\r", "\n"})
LENGTH_ABBREVIATIONS = (
    (r"Graphite-Crucible", "Graphite"),
    (r"Carrier-Tray", "Tray"),
    (r"Cooling-Water", "Cool-Water"),
    (r"Communication", "Comm"),
    (r"Initialization", "Init"),
    (r"Temperature", "Temp"),
    (r"Confirmation", "Confirm"),
    (r"Completed", "Done"),
    (r"Complete", "Done"),
    (r"Cylinder", "Cyl"),
    (r"Position", "Pos"),
    (r"Station", "Stn"),
    (r"Sensor", "Snsr"),
    (r"Abnormal", "Fault"),
    (r"Failure", "Fail"),
    (r"Command", "Cmd"),
    (r"Weighing", "Weigh"),
)


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _valid_target(target: str) -> bool:
    return (
        bool(target)
        and target == target.strip()
        and len(target) <= MAX_COMMENT_LENGTH
        and all(32 <= ord(character) <= 126 for character in target)
        and not any(character in target for character in FORBIDDEN)
        and CJK_RE.search(target) is None
    )


def _shorten_ascii_target(target: str) -> str:
    """Deterministically abbreviate an otherwise valid overlength PLC label."""
    candidate = target.strip()
    if (
        len(candidate) <= MAX_COMMENT_LENGTH
        or not candidate.isascii()
        or any(character in candidate for character in FORBIDDEN)
    ):
        return candidate
    for pattern, replacement in LENGTH_ABBREVIATIONS:
        candidate = re.sub(pattern, replacement, candidate, flags=re.IGNORECASE)
        candidate = " ".join(candidate.split())
        if len(candidate) <= MAX_COMMENT_LENGTH:
            return candidate
    return candidate


def _validate_target(source: str, target: str, label: str) -> None:
    if not _valid_target(target):
        raise ValueError(
            f"{label} translation for {source!r} must be non-empty trimmed ASCII, "
            f"at most {MAX_COMMENT_LENGTH} characters, with no comma, quote, or newline: "
            f"{target!r}"
        )


def _load_glossary(path: Path) -> dict[str, str]:
    value = _load_object(path)
    result: dict[str, str] = {}
    for source, target in value.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("project glossary must contain only string-to-string entries")
        target = target.strip()
        if _valid_target(target):
            result[source] = target
    return result


def _load_inventory(path: Path) -> tuple[list[dict[str, Any]], int]:
    payload = _load_object(path)
    raw_items = payload.get("items", payload.get("sources"))
    if not isinstance(raw_items, list):
        raise ValueError("device-comment inventory has no items or sources array")

    items: list[dict[str, Any]] = []
    skipped_non_cjk = 0
    seen_sources: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise ValueError(f"device-comment inventory item {index} is not an object")
        source = raw_item.get("source", raw_item.get("source_text"))
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"device-comment inventory item {index} has no source")
        if source in seen_sources:
            raise ValueError(f"device-comment inventory source is not unique: {source!r}")
        seen_sources.add(source)
        if CJK_RE.search(source) is None:
            skipped_non_cjk += 1
            continue

        locations = raw_item.get("addresses", raw_item.get("rows"))
        if locations is None:
            raise ValueError(
                f"device-comment item {source!r} requires addresses or rows context"
            )
        if "context_samples" in raw_item:
            context = raw_item["context_samples"]
        elif "context" in raw_item:
            context = raw_item["context"]
        elif "contexts" in raw_item:
            context = raw_item["contexts"]
        else:
            raise ValueError(
                f"device-comment item {source!r} requires context_samples, context, or contexts"
            )
        reuse_target = raw_item.get("reuse_target")
        if reuse_target is not None:
            if not isinstance(reuse_target, str):
                raise ValueError(f"reuse_target must be a string for {source!r}")
            _validate_target(source, reuse_target, "inventory reuse_target")

        record_id = raw_item.get("id")
        if not isinstance(record_id, str) or not record_id:
            record_id = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
        items.append(
            {
                "id": record_id,
                "source": source,
                "locations": locations,
                "context": context,
                "reuse_target": reuse_target,
            }
        )
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("device-comment inventory contains duplicate item IDs")
    return items, skipped_non_cjk


def _seed_translations(
    items: list[dict[str, Any]], glossary: dict[str, str]
) -> tuple[dict[str, str], dict[str, int]]:
    candidates: dict[str, list[tuple[str, str]]] = {}

    def add(source: str, target: str, origin: str) -> None:
        _validate_target(source, target, origin)
        candidates.setdefault(source, []).append((target, origin))

    reuse_matches = 0
    glossary_matches = 0
    for item in items:
        source = str(item["source"])
        reuse_target = item.get("reuse_target")
        if isinstance(reuse_target, str):
            add(source, reuse_target, "inventory reuse_target")
            reuse_matches += 1
        if source in glossary:
            add(source, glossary[source], "project glossary")
            glossary_matches += 1

    seeds: dict[str, str] = {}
    for source, values in candidates.items():
        targets = {target for target, _origin in values}
        if len(targets) != 1:
            detail = ", ".join(f"{origin}={target!r}" for target, origin in values)
            raise ValueError(f"conflicting seeded translations for {source!r}: {detail}")
        seeds[source] = targets.pop()
    return seeds, {
        "reuse_target_matches": reuse_matches,
        "glossary_matches": glossary_matches,
        "unique_seeded": len(seeds),
    }


def _load_existing(
    path: Path, known_sources: set[str], inventory_sha256: str
) -> tuple[dict[str, str], int]:
    if not path.exists():
        return {}, 0
    payload = _load_object(path)
    raw_translations = payload.get("translations")
    progress = payload.get("progress")
    if not isinstance(raw_translations, dict) or not isinstance(progress, dict):
        raise ValueError("resume file requires translations and progress objects")
    if progress.get("inventory_sha256") != inventory_sha256:
        raise ValueError("resume file was created from a different device-comment inventory")
    translations: dict[str, str] = {}
    for source, target in raw_translations.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("resume translations must be string-to-string entries")
        if source not in known_sources:
            raise ValueError(f"resume file contains an unknown device comment: {source!r}")
        _validate_target(source, target, "resume file")
        translations[source] = target
    total_batches = progress.get("batches_completed_total", 0)
    if not isinstance(total_batches, int) or total_batches < 0:
        raise ValueError("resume batches_completed_total must be a non-negative integer")
    return translations, total_batches


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _compact_json(value: object, *, limit: int = 10_000) -> str:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 18] + "...[context cut]"


def _item_context(item: dict[str, Any], project_context: str) -> str:
    location_parts: list[str] = []
    for raw in item["locations"][:3]:
        if not isinstance(raw, dict):
            location_parts.append(str(raw))
            continue
        part = str(raw.get("address", ""))
        adjacent = raw.get("adjacent_same_prefix")
        if isinstance(adjacent, dict):
            neighbours = []
            for direction in ("previous", "next"):
                neighbour = adjacent.get(direction)
                if isinstance(neighbour, dict) and neighbour.get("source_text"):
                    neighbours.append(f"{direction}={neighbour['source_text']}")
            if neighbours:
                part += " [" + "; ".join(neighbours) + "]"
        location_parts.append(part)

    logic_parts: list[str] = []
    raw_context = item["context"]
    context_records = raw_context[:3] if isinstance(raw_context, list) else [raw_context]
    for raw in context_records:
        if not isinstance(raw, dict):
            continue
        heading = raw.get("nearest_heading")
        heading_text = heading.get("text", "") if isinstance(heading, dict) else ""
        logic_parts.append(
            " | ".join(
                value
                for value in (
                    str(raw.get("module", raw.get("module_file", ""))),
                    str(heading_text),
                    str(raw.get("instruction", "")),
                )
                if value
            )
        )
    return "\n".join(
        [
            "Domain: precious-metal kiosk; robot arm; coarse/fine weighing; induction "
            "furnace; XRF assay.",
            "Translate this KV PLC device comment into concise industrial English.",
            "ASCII only; 1-32 chars; no comma/quotes/newline. Aim <=28 chars; abbreviate.",
            "Addresses/neighbours: " + ("; ".join(location_parts) or "none"),
            "Mnemonic and neighboring-comment context: "
            + ("; ".join(logic_parts) or _compact_json(raw_context, limit=500)),
        ]
    )


def _payload(
    *,
    inventory_path: Path,
    items: list[dict[str, Any]],
    translations: dict[str, str],
    seeds: dict[str, str],
    seed_counts: dict[str, int],
    skipped_non_cjk: int,
    batch_size: int,
    total_batches: int,
    run_batches: int,
) -> dict[str, Any]:
    ordered = {
        str(item["source"]): translations[str(item["source"])]
        for item in items
        if str(item["source"]) in translations
    }
    completed = len(ordered)
    return {
        "schema_version": 1,
        "artifact_type": "device_comment_english_translations",
        "constraints": {
            "encoding": "ASCII",
            "maximum_characters": MAX_COMMENT_LENGTH,
            "forbidden_characters": [",", "single_quote", "double_quote", "CR", "LF"],
        },
        "translations": ordered,
        "progress": {
            "inventory_path": inventory_path.as_posix(),
            "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            "batch_size": batch_size,
            "total": len(items),
            "skipped_non_cjk": skipped_non_cjk,
            "completed": completed,
            "remaining": len(items) - completed,
            "complete": completed == len(items),
            "seed_counts": seed_counts,
            "seeded_unique_completed": sum(source in ordered for source in seeds),
            "llm_completed": sum(source not in seeds for source in ordered),
            "batches_completed_total": total_batches,
            "batches_completed_this_run": run_batches,
        },
    }


def run(
    *,
    inventory_path: Path,
    output_path: Path,
    glossary_path: Path,
    batch_size: int,
    max_batches: int,
    config_path: Path | None = None,
    env_path: Path | None = None,
    provider: TranslationProvider | None = None,
    project_context: str | None = None,
) -> dict[str, Any]:
    if not 1 <= batch_size <= 200:
        raise ValueError("batch_size must be between 1 and 200")
    if max_batches < 0:
        raise ValueError("max_batches must be non-negative")
    items, skipped_non_cjk = _load_inventory(inventory_path)
    glossary = _load_glossary(glossary_path)
    seeds, seed_counts = _seed_translations(items, glossary)
    inventory_sha256 = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    known_sources = {str(item["source"]) for item in items}
    translations, total_batches = _load_existing(
        output_path, known_sources, inventory_sha256
    )
    for source, target in seeds.items():
        existing = translations.get(source)
        if existing is not None and existing != target:
            raise ValueError(
                f"resume translation conflicts with a seed for {source!r}: "
                f"{existing!r} != {target!r}"
            )
        translations[source] = target

    run_batches = 0
    state = _payload(
        inventory_path=inventory_path,
        items=items,
        translations=translations,
        seeds=seeds,
        seed_counts=seed_counts,
        skipped_non_cjk=skipped_non_cjk,
        batch_size=batch_size,
        total_batches=total_batches,
        run_batches=run_batches,
    )
    _atomic_write_json(output_path, state)

    pending = [item for item in items if str(item["source"]) not in translations]
    if pending and max_batches:
        if provider is None:
            config = load_config(config_path, env_path)
            provider = OpenAICompatibleProvider(
                OpenAICompatibleConfig(
                    base_url=config.llm.endpoint,
                    model=config.llm.model,
                    api_key=config.llm.api_key or "",
                    timeout_seconds=config.llm.timeout_seconds,
                    temperature=0.0,
                )
            )
            project_context = config.translation.project_context
        if not project_context or not project_context.strip():
            raise ValueError("translation project context must not be empty")

        limit = min(len(pending), max_batches * batch_size)
        for offset in range(0, limit, batch_size):
            batch = pending[offset : offset + batch_size]
            batch_results: dict[str, str] = {}
            retry_items = list(batch)
            previous_invalid: dict[str, str] = {}
            for attempt in range(3):
                requests = []
                for item in retry_items:
                    context = _item_context(item, project_context)
                    record_id = str(item["id"])
                    if record_id in previous_invalid:
                        invalid = previous_invalid[record_id]
                        context += (
                            "\nREPAIR REQUIRED: the previous answer was invalid: "
                            f"{json.dumps(invalid, ensure_ascii=False)} "
                            f"({len(invalid)} characters). Return a shorter plain ASCII label "
                            "that satisfies every hard constraint."
                        )
                    requests.append(
                        ProviderBatchItem(
                            record_id=record_id,
                            text=str(item["source"]),
                            context=context,
                        )
                    )
                # The long-form project glossary is intentionally not forced verbatim here:
                # combining its station names can exceed KV's 32-character device-comment limit.
                results = provider.translate_batch(requests, glossary={})
                invalid_items: list[dict[str, Any]] = []
                for item in retry_items:
                    source = str(item["source"])
                    record_id = str(item["id"])
                    result = results.get(record_id)
                    if result is None:
                        raise ValueError(f"provider omitted translation for {source!r}")
                    target = _shorten_ascii_target(result.translation)
                    if _valid_target(target):
                        batch_results[source] = target
                    else:
                        previous_invalid[record_id] = target
                        invalid_items.append(item)
                if not invalid_items:
                    break
                if attempt == 2:
                    failed = invalid_items[0]
                    failed_source = str(failed["source"])
                    _validate_target(
                        failed_source,
                        previous_invalid[str(failed["id"])],
                        "provider response after repair retries",
                    )
                retry_items = invalid_items
            for source, target in batch_results.items():
                existing = translations.get(source)
                if existing is not None and existing != target:
                    raise ValueError(f"provider produced a conflict for {source!r}")
                translations[source] = target
            run_batches += 1
            total_batches += 1
            state = _payload(
                inventory_path=inventory_path,
                items=items,
                translations=translations,
                seeds=seeds,
                seed_counts=seed_counts,
                skipped_non_cjk=skipped_non_cjk,
                batch_size=batch_size,
                total_batches=total_batches,
                run_batches=run_batches,
            )
            _atomic_write_json(output_path, state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=ROOT / ".autocomp/device-comment-inventory.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".autocomp/device-comment-translations.json",
    )
    parser.add_argument(
        "--glossary", type=Path, default=ROOT / "reports/02-project-glossary.json"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.json")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-batches", type=int, default=3)
    args = parser.parse_args(argv)
    state = run(
        inventory_path=args.inventory.resolve(),
        output_path=args.output.resolve(),
        glossary_path=args.glossary.resolve(),
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        config_path=args.config.resolve(),
        env_path=args.env_file.resolve(),
    )
    print(json.dumps(state["progress"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
