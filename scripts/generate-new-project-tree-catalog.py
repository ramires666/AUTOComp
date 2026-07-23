"""Build the clean project's exact tree catalog with prior translations as references."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _walk(nodes: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    for node in nodes:
        yield node
        yield from _walk(node.get("children", []))


def main() -> int:
    response = _read(".autocomp/09-new-project-tree.json")
    inventory = response["project_tree_inventory"]
    if not inventory.get("complete") or not inventory.get("restoration_complete"):
        raise RuntimeError("new project tree inventory is incomplete or unrestored")
    nodes = list(_walk(inventory["roots"]))
    prior = _read("reports/04-full-tree-bilingual-catalog.json")
    prior_by_locator = {tuple(item["locator"]): item for item in prior["records"]}
    if len(nodes) != len(prior_by_locator):
        raise RuntimeError("new and prior tree node counts differ")

    records: list[dict[str, Any]] = []
    for node in nodes:
        locator = tuple(node["locator"])
        reference = prior_by_locator.get(locator)
        if reference is None:
            raise RuntimeError(f"new tree locator has no prior reference: {locator}")
        english = reference["english_name"]
        if locator == (4,):
            english = str(node["name"]).replace("程序:", "Programs:", 1)
        records.append(
            {
                "locator": list(locator),
                "category": reference["category"],
                "original_name": node["name"],
                "current_tree_name": node["name"],
                "english_name": english,
                "russian_name": reference.get("russian_name", ""),
                "english_status": "reference",
                "translation_record_id": reference.get("translation_record_id"),
                "original_path": node["path"],
            }
        )

    by_locator = {tuple(item["locator"]): item for item in records}
    for record in records:
        locator = tuple(record["locator"])
        prefixes = [locator[:depth] for depth in range(1, len(locator) + 1)]
        record["english_path"] = [by_locator[prefix]["english_name"] for prefix in prefixes]

    categories = Counter(record["category"] for record in records)
    result = {
        "schema_version": 1,
        "artifact_type": "clean_project_tree_multilingual_catalog",
        "purpose": (
            "Exact clean-project tree; English values are prior reviewed references only. "
            "No values were written back to KV STUDIO."
        ),
        "provenance": {
            "exact_tree_inventory": ".autocomp/09-new-project-tree.json",
            "english_reference": "reports/04-full-tree-bilingual-catalog.json",
            "project_window_title": inventory["window_title"],
        },
        "summary": {
            "total_nodes": len(records),
            "restored_expansions": inventory["restored_count"],
            "by_category": dict(sorted(categories.items())),
        },
        "records": records,
    }
    output = ROOT / "reports/09-new-project-tree-multilingual.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
