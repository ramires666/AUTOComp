"""Audit remaining CJK text in the full project tree after checkpoint 02 translation."""

from __future__ import annotations

import json
import re
from pathlib import Path

project = Path(__file__).resolve().parent.parent
tree_path = project / ".autocomp" / "live-full-tree.json"
inventory_path = project / "reports" / "02-tree-translation-inventory.json"
output_path = project / "reports" / "03-remaining-cjk-audit.json"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

tree = json.loads(tree_path.read_text(encoding="utf-8"))
inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
translated_sources = {record["source_text"] for record in inventory}

program_containers = {"每次扫描执行型模块", "后备模块"}
program_children = {"局部标号", "书签"}

remaining = []


def visit(node: dict, path: list[str]) -> None:
    name = node["name"]
    current_path = path + [name]
    locator = node["locator"]

    # Skip localized UI nodes that are not project-owned names
    if name in {"项目", "单元配置", "CPU 系统设定", "局部标号", "书签"}:
        return

    # Program nodes are under containers and have exactly the expected children
    is_program = (
        len(current_path) >= 3
        and current_path[-2] in program_containers
        and len(node["children"]) == 2
        and {child["name"] for child in node["children"]} == program_children
    )

    # Bookmarks are direct children of 书签
    is_bookmark = len(current_path) >= 2 and current_path[-2] == "书签"

    if _CJK_RE.search(name) and not (is_program or is_bookmark):
        remaining.append(
            {
                "text": name,
                "path": current_path,
                "locator": locator,
                "category": "program" if is_program else "bookmark" if is_bookmark else "other",
            }
        )
    elif _CJK_RE.search(name) and name not in translated_sources:
        remaining.append(
            {
                "text": name,
                "path": current_path,
                "locator": locator,
                "category": "program" if is_program else "bookmark" if is_bookmark else "other",
                "status": "not_in_translation_inventory",
            }
        )

    for child in node["children"]:
        visit(child, current_path)


for root in tree["project_tree_inventory"]["roots"]:
    visit(root, [])

payload = {
    "schema_version": 1,
    "artifact_type": "remaining_cjk_audit",
    "checkpoint": "03-bookmarks-approved",
    "tree_items": tree["project_tree_inventory"]["item_count"],
    "translated_inventory_items": len(translated_sources),
    "remaining_cjk_nodes": len(remaining),
    "items": remaining,
}

output_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"Remaining CJK nodes: {len(remaining)}")
