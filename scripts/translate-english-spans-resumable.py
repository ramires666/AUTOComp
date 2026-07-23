"""Resume-safe English translation of low-risk comment/label CJK spans."""

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

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
BATCH_SIZE = 25


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _load_string_map(path: Path, label: str) -> dict[str, str]:
    value = _load_object(path)
    result: dict[str, str] = {}
    for source, target in value.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError(f"{label} must contain only string-to-string entries")
        _validate_target(source, target, label)
        result[source] = target.strip()
    return result


def _validate_target(source: str, target: str, label: str) -> None:
    if not target.strip():
        raise ValueError(f"{label} has an empty translation for {source!r}")
    if CJK_RE.search(target):
        raise ValueError(f"{label} translation still contains CJK for {source!r}")


def _load_inventory(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    payload = _load_object(path)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("translation inventory has no items array")

    eligible: list[dict[str, Any]] = []
    skipped = {"program_module_name": 0, "operator_voice_string": 0}
    seen_sources: set[str] = set()
    seen_ids: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("translation inventory item must be an object")
        source = raw_item.get("source")
        record_id = raw_item.get("id")
        kind = raw_item.get("kind")
        if not isinstance(source, str) or CJK_RE.fullmatch(source) is None:
            raise ValueError(f"inventory source is not one exact CJK span: {source!r}")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"inventory item has no stable id: {source!r}")
        if source in seen_sources or record_id in seen_ids:
            raise ValueError(f"inventory has duplicate source or id: {source!r}")
        seen_sources.add(source)
        seen_ids.add(record_id)
        if kind == "comment_or_label":
            reuse_target = raw_item.get("reuse_target")
            if reuse_target is not None:
                if not isinstance(reuse_target, str):
                    raise ValueError(f"reuse_target must be a string: {source!r}")
                _validate_target(source, reuse_target, "inventory reuse_target")
            eligible.append(raw_item)
        elif kind in skipped:
            skipped[kind] += 1
        else:
            raise ValueError(f"unsupported inventory kind {kind!r} for {source!r}")
    return eligible, skipped


def _seed_translations(
    eligible: list[dict[str, Any]],
    glossary: dict[str, str],
    memory: dict[str, str],
) -> tuple[dict[str, str], dict[str, int]]:
    eligible_sources = {str(item["source"]) for item in eligible}
    candidates: dict[str, list[tuple[str, str]]] = {}

    def add(source: str, target: str, origin: str) -> None:
        _validate_target(source, target, origin)
        candidates.setdefault(source, []).append((target.strip(), origin))

    reuse_count = 0
    for item in eligible:
        target = item.get("reuse_target")
        if isinstance(target, str):
            add(str(item["source"]), target, "inventory reuse_target")
            reuse_count += 1
    glossary_count = 0
    for source in eligible_sources.intersection(glossary):
        add(source, glossary[source], "project glossary")
        glossary_count += 1
    memory_count = 0
    for source in eligible_sources.intersection(memory):
        add(source, memory[source], "translation memory")
        memory_count += 1

    seeds: dict[str, str] = {}
    for source, values in candidates.items():
        targets = {target for target, _origin in values}
        if len(targets) != 1:
            detail = ", ".join(f"{origin}={target!r}" for target, origin in values)
            raise ValueError(f"conflicting seeded translations for {source!r}: {detail}")
        seeds[source] = targets.pop()
    return seeds, {
        "reuse_target_matches": reuse_count,
        "glossary_matches": glossary_count,
        "memory_matches": memory_count,
        "unique_seeded": len(seeds),
    }


def _load_existing(
    path: Path, eligible_sources: set[str], inventory_sha256: str
) -> tuple[dict[str, str], int]:
    if not path.exists():
        return {}, 0
    payload = _load_object(path)
    raw_translations = payload.get("translations")
    if not isinstance(raw_translations, dict):
        raise ValueError("resume file has no translations object")
    translations: dict[str, str] = {}
    for source, target in raw_translations.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("resume translations must be string-to-string entries")
        if source not in eligible_sources:
            raise ValueError(f"resume file contains a skipped or unknown source: {source!r}")
        _validate_target(source, target, "resume file")
        translations[source] = target.strip()
    progress = payload.get("progress")
    if not isinstance(progress, dict):
        raise ValueError("resume file has no progress object")
    if progress.get("inventory_sha256") != inventory_sha256:
        raise ValueError("resume file was created from a different translation inventory")
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


def _item_context(item: dict[str, Any], project_context: str) -> str:
    context = item.get("context")
    programs = context.get("programs", []) if isinstance(context, dict) else []
    program_lines = []
    for program in programs[:4]:
        if not isinstance(program, dict):
            continue
        program_lines.append(
            f"- {program.get('file', '')}: {program.get('original_name', '')} "
            f"({program.get('english_name', '')})"
        )
    examples = []
    occurrences = item.get("occurrences", [])
    if isinstance(occurrences, list):
        for occurrence in occurrences[:4]:
            if not isinstance(occurrence, dict):
                continue
            lines = occurrence.get("context", {})
            current = lines.get("current_line") if isinstance(lines, dict) else None
            if isinstance(current, str) and current not in examples:
                examples.append(current)
    return "\n".join(
        [
            f"Project/domain context: {project_context.strip()}",
            "Item type: exact Chinese span from a PLC comment or label; translate only this span.",
            "Programs:",
            *(program_lines or ["- unavailable"]),
            "Representative source lines:",
            *(f"- {line}" for line in examples),
        ]
    )


def _payload(
    *,
    inventory_path: Path,
    eligible: list[dict[str, Any]],
    skipped: dict[str, int],
    translations: dict[str, str],
    seeds: dict[str, str],
    seed_counts: dict[str, int],
    total_batches: int,
    run_batches: int,
) -> dict[str, Any]:
    ordered_translations = {
        str(item["source"]): translations[str(item["source"])]
        for item in eligible
        if str(item["source"]) in translations
    }
    eligible_total = len(eligible)
    completed = len(ordered_translations)
    inventory_sha256 = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "artifact_type": "english_span_translations",
        "translations": ordered_translations,
        "progress": {
            "inventory_path": inventory_path.as_posix(),
            "inventory_sha256": inventory_sha256,
            "batch_size": BATCH_SIZE,
            "eligible_kind": "comment_or_label",
            "eligible_total": eligible_total,
            "completed": completed,
            "remaining": eligible_total - completed,
            "complete": completed == eligible_total,
            "seed_counts": seed_counts,
            "seeded_unique_completed": sum(source in ordered_translations for source in seeds),
            "llm_completed": sum(source not in seeds for source in ordered_translations),
            "batches_completed_total": total_batches,
            "batches_completed_this_run": run_batches,
            "skipped": skipped,
        },
    }


def run(
    *,
    inventory_path: Path,
    output_path: Path,
    glossary_path: Path,
    memory_path: Path,
    max_batches: int,
    config_path: Path | None = None,
    env_path: Path | None = None,
    provider: TranslationProvider | None = None,
    project_context: str | None = None,
) -> dict[str, Any]:
    if max_batches < 0:
        raise ValueError("max_batches must be non-negative")
    eligible, skipped = _load_inventory(inventory_path)
    glossary = _load_string_map(glossary_path, "project glossary")
    memory = _load_string_map(memory_path, "translation memory")
    seeds, seed_counts = _seed_translations(eligible, glossary, memory)
    eligible_sources = {str(item["source"]) for item in eligible}
    inventory_sha256 = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    translations, total_batches = _load_existing(
        output_path, eligible_sources, inventory_sha256
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
        eligible=eligible,
        skipped=skipped,
        translations=translations,
        seeds=seeds,
        seed_counts=seed_counts,
        total_batches=total_batches,
        run_batches=run_batches,
    )
    _atomic_write_json(output_path, state)

    pending = [item for item in eligible if str(item["source"]) not in translations]
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

        for offset in range(0, min(len(pending), max_batches * BATCH_SIZE), BATCH_SIZE):
            batch = pending[offset : offset + BATCH_SIZE]
            request_items = [
                ProviderBatchItem(
                    record_id=str(item["id"]),
                    text=str(item["source"]),
                    context=_item_context(item, project_context),
                )
                for item in batch
            ]
            results = provider.translate_batch(request_items, glossary=glossary)
            for item in batch:
                source = str(item["source"])
                result = results.get(str(item["id"]))
                if result is None:
                    raise ValueError(f"provider omitted translation for {source!r}")
                target = result.translation.strip()
                _validate_target(source, target, "provider response")
                if source in translations and translations[source] != target:
                    raise ValueError(f"provider produced a conflicting translation for {source!r}")
                translations[source] = target
            run_batches += 1
            total_batches += 1
            state = _payload(
                inventory_path=inventory_path,
                eligible=eligible,
                skipped=skipped,
                translations=translations,
                seeds=seeds,
                seed_counts=seed_counts,
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
        default=ROOT / ".autocomp/english-translation-inventory.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".autocomp/english-span-translations.json",
    )
    parser.add_argument(
        "--glossary", type=Path, default=ROOT / "reports/02-project-glossary.json"
    )
    parser.add_argument(
        "--memory", type=Path, default=ROOT / "reports/02-tree-translation-memory.json"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.json")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--max-batches", type=int, default=3)
    args = parser.parse_args(argv)
    state = run(
        inventory_path=args.inventory.resolve(),
        output_path=args.output.resolve(),
        glossary_path=args.glossary.resolve(),
        memory_path=args.memory.resolve(),
        max_batches=args.max_batches,
        config_path=args.config.resolve(),
        env_path=args.env_file.resolve(),
    )
    print(json.dumps(state["progress"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
