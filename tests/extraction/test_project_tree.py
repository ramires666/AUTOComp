from __future__ import annotations

from copy import deepcopy

import pytest

from autocomp.extraction.project_tree import (
    ProjectTreeExtractionError,
    extract_project_tree_inventory,
)
from autocomp.translation.models import RiskLevel, TextKind


def _node(name: str, locator: list[int], children: list[dict] | None = None) -> dict:
    children = children or []
    return {
        "name": name,
        "path": [],  # Filled recursively by _document.
        "depth": len(locator) - 1,
        "sibling_index": locator[-1],
        "locator": locator,
        "initial_expansion_state": "leaf" if not children else "expanded",
        "expanded_for_inventory": False,
        "visible": True,
        "truncated": False,
        "children": children,
    }


def _document() -> dict:
    program = _node(
        "通信程序 A1",
        [1, 0, 0],
        [
            _node("局部标号", [1, 0, 0, 0]),
            _node(
                "书签",
                [1, 0, 0, 1],
                [
                    _node("/*启动 X0*/", [1, 0, 0, 1, 0]),
                    _node("Already English", [1, 0, 0, 1, 1]),
                ],
            ),
        ],
    )
    fixed = _node("软元件注释", [0])
    branch = _node("每次扫描执行型模块", [1, 0], [program])
    program_root = _node("程序: Test", [1], [branch])
    roots = [fixed, program_root]

    def fill_path(node: dict, parent: list[str]) -> int:
        node["path"] = parent + [node["name"]]
        return 1 + sum(fill_path(child, node["path"]) for child in node["children"])

    count = sum(fill_path(root, []) for root in roots)
    return {
        "schema_version": 1,
        "action": "inventory_project_tree",
        "checkpoint": "test",
        "inventory": {
            "item_count": count,
            "complete": True,
            "truncated": False,
            "restore_requested": True,
            "restoration_complete": True,
            "warnings": [],
            "roots": roots,
        },
    }


def test_extracts_only_structural_program_and_direct_cjk_bookmark() -> None:
    records = extract_project_tree_inventory(_document(), source_name="tree.json")

    assert [record.source_text for record in records] == ["通信程序 A1", "/*启动 X0*/"]
    assert records[0].kind is TextKind.PROGRAM_NAME
    assert records[0].risk is RiskLevel.HIGH
    assert records[0].requires_review is True
    assert records[1].kind is TextKind.COMMENT
    assert records[1].risk is RiskLevel.LOW
    assert "locator:1.0.0" in records[0].hierarchy
    assert "locator=1.0.0.1.0" in records[1].context
    assert records[1].location == "tree.json#locator=1.0.0.1.0"
    assert all(record.source_text != "软元件注释" for record in records)


def test_record_ids_depend_only_on_locator_kind_and_text() -> None:
    first = extract_project_tree_inventory(_document(), source_name="one.json")
    second = extract_project_tree_inventory(_document(), source_name="two.json")
    assert [record.record_id for record in first] == [record.record_id for record in second]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("complete", False, "incomplete"),
        ("truncated", True, "truncated"),
        ("restoration_complete", False, "restoration"),
        ("warnings", ["partial"], "warnings"),
    ],
)
def test_refuses_unsafe_inventory(field: str, value: object, message: str) -> None:
    payload = _document()
    payload["inventory"][field] = value
    with pytest.raises(ProjectTreeExtractionError, match=message):
        extract_project_tree_inventory(payload)


@pytest.mark.parametrize("damage", ["count", "locator", "index", "path", "node_truncated"])
def test_refuses_recursive_structure_mismatch(damage: str) -> None:
    payload = deepcopy(_document())
    program = payload["inventory"]["roots"][1]["children"][0]["children"][0]
    if damage == "count":
        payload["inventory"]["item_count"] += 1
    elif damage == "locator":
        program["locator"] = [1, 0, 9]
    elif damage == "index":
        program["sibling_index"] = 9
    elif damage == "path":
        program["path"][-1] = "changed"
    else:
        program["children"][1]["children"][0]["truncated"] = True

    with pytest.raises(ProjectTreeExtractionError):
        extract_project_tree_inventory(payload)


def test_similar_ui_branch_is_not_mistaken_for_program() -> None:
    payload = _document()
    fake = _node("固定中文标签", [2], [_node("书签", [2, 0])])
    fake["path"] = [fake["name"]]
    fake["children"][0]["path"] = [fake["name"], "书签"]
    payload["inventory"]["roots"].append(fake)
    payload["inventory"]["item_count"] += 2

    records = extract_project_tree_inventory(payload)
    assert all(record.source_text != "固定中文标签" for record in records)
