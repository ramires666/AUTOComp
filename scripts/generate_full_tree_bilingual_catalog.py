"""Generate the complete original/English tree catalog with Russian slots."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCATOR_RE = re.compile(r"locator=([0-9.]+)")
SYSTEM_ENGLISH = {
    "单元配置": "Unit Configuration",
    "软元件注释": "Device Comments",
    "标号": "Labels",
    "CPU 系统设定": "CPU System Settings",
    "每次扫描执行型模块": "Every-Scan Execution Modules",
    "局部标号": "Local Labels",
    "书签": "Bookmarks",
    "后备模块": "Standby Modules",
    "子程序型宏": "Subroutine Macros",
    "初始化模块": "Initialization Modules",
    "软元件初始值": "Device Initial Values",
    "自保持型宏": "Retentive Macros",
    "宏": "Macros",
}
REVIEWED_OVERRIDES = {
    "A_52号指令:（10#-21#：测金，石墨，载盘）": (
        "A_52 Command: (10#-21#: XRF Assay, Graphite-Crucible, "
        "and Carrier-Tray Handling)"
    ),
    "A_54号指令:（在工控指令）": "A_54 Command: (IPC Command)",
    "MQTT:4G通信模块": "MQTT: 4G Communication Module",
    "夹子气缸老化": "Gripper-Cylinder Aging Test",
}


def _read(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _walk(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        result.append(node)
        result.extend(_walk(node.get("children", [])))
    return result


def _system_english(source: str) -> str:
    if source in SYSTEM_ENGLISH:
        return SYSTEM_ENGLISH[source]
    if source.startswith("程序:"):
        return "Program:" + source.removeprefix("程序:")
    return source.replace("[预留]", "[Reserved]")


def main() -> None:
    post_payload = _read(".autocomp/post-translation-tree.json")
    post = post_payload["project_tree_inventory"]
    inventory = _read("reports/02-tree-translation-inventory.json")
    manifest = _read("reports/02-tree-translation-manifest.json")
    approved = _read("reports/03-approved-ui-rename-manifest.json")

    decisions = {
        item["record_id"]: item
        for item in manifest["decisions"]
        if isinstance(item.get("target_text"), str)
    }
    translations: dict[tuple[int, ...], dict[str, str]] = {}
    for item in inventory:
        match = LOCATOR_RE.search(item["location"])
        decision = decisions.get(item["record_id"])
        if not match or not decision:
            continue
        locator = tuple(int(part) for part in match.group(1).split("."))
        translations[locator] = {
            "record_id": item["record_id"],
            "source": item["source_text"],
            "target": REVIEWED_OVERRIDES.get(
                item["source_text"], decision["target_text"]
            ),
        }
    applied = {tuple(item["locator"]): item for item in approved["items"]}

    draft: list[dict[str, Any]] = []
    original_by_locator: dict[tuple[int, ...], str] = {}
    english_by_locator: dict[tuple[int, ...], str] = {}
    for node in _walk(post["roots"]):
        locator = tuple(node["locator"])
        translation = translations.get(locator)
        approved_item = applied.get(locator)
        original = (
            approved_item["expected_source"]
            if approved_item
            else translation["source"]
            if translation
            else node["name"]
        )
        if approved_item:
            english, status, category = approved_item["target"], "applied", "user_text"
        elif translation and len(locator) == 3 and locator[:2] == (4, 0):
            english, status, category = translation["target"], "planned", "program"
        else:
            english = _system_english(original)
            if english != original:
                status, category = "reference", "system_ui"
            else:
                english, status, category = node["name"], "unchanged", "project_structure"
        original_by_locator[locator] = original
        english_by_locator[locator] = english
        draft.append(
            {
                "locator": list(locator),
                "category": category,
                "original_name": original,
                "english_name": english,
                "russian_name": "",
                "english_status": status,
                "current_tree_name": node["name"],
                "translation_record_id": translation["record_id"] if translation else None,
            }
        )

    for record in draft:
        locator = tuple(record["locator"])
        prefixes = [locator[:depth] for depth in range(1, len(locator) + 1)]
        record["original_path"] = [original_by_locator[prefix] for prefix in prefixes]
        record["english_path"] = [english_by_locator[prefix] for prefix in prefixes]

    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for record in draft:
        by_status[record["english_status"]] = by_status.get(record["english_status"], 0) + 1
        by_category[record["category"]] = by_category.get(record["category"], 0) + 1
    catalog = {
        "schema_version": 1,
        "artifact_type": "full_tree_multilingual_catalog",
        "purpose": (
            "Preserve every captured tree label and path in its original form, "
            "the applied/planned English equivalent, and an empty Russian field "
            "for migration to global KV STUDIO."
        ),
        "provenance": {
            "post_bookmark_tree": ".autocomp/post-translation-tree.json",
            "translation_inventory": "reports/02-tree-translation-inventory.json",
            "translation_manifest": "reports/02-tree-translation-manifest.json",
            "approved_bookmark_manifest": "reports/03-approved-ui-rename-manifest.json",
        },
        "summary": {
            "total_nodes": len(draft),
            "english_applied": by_status.get("applied", 0),
            "english_planned": by_status.get("planned", 0),
            "system_reference_translations": by_status.get("reference", 0),
            "russian_pending": sum(not record["russian_name"] for record in draft),
            "by_status": by_status,
            "by_category": by_category,
        },
        "records": draft,
    }
    if len(draft) != int(post["item_count"]):
        raise RuntimeError("catalog node count does not match the complete tree inventory")
    output = ROOT / "reports/04-full-tree-bilingual-catalog.json"
    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(catalog["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
