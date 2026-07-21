"""Deterministic extraction from a complete project-tree inventory (schema v1)."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from autocomp.translation.inventory import contains_cjk, with_assessed_risk
from autocomp.translation.models import InventoryRecord, TextKind

_PROGRAM_CHILDREN = frozenset({"局部标号", "书签"})
_PROGRAM_CONTAINERS = frozenset({"每次扫描执行型模块", "后备模块"})


class ProjectTreeExtractionError(ValueError):
    """Raised when an inventory is incomplete or structurally inconsistent."""


@dataclass(frozen=True, slots=True)
class _Node:
    name: str
    path: tuple[str, ...]
    locator: tuple[int, ...]
    children: tuple[_Node, ...]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectTreeExtractionError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProjectTreeExtractionError(f"{label} must be an array")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectTreeExtractionError(f"{label} must be an integer")
    return value


def _parse_node(
    raw_value: Any,
    *,
    expected_parent_path: tuple[str, ...],
    expected_parent_locator: tuple[int, ...],
    sibling_index: int,
) -> tuple[_Node, int]:
    raw = _mapping(raw_value, "tree node")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ProjectTreeExtractionError("tree node name must be a non-empty string")

    expected_path = expected_parent_path + (name,)
    path_value = _list(raw.get("path"), f"path for {name!r}")
    if not all(isinstance(part, str) for part in path_value):
        raise ProjectTreeExtractionError(f"path for {name!r} must contain only strings")
    path = tuple(path_value)
    if path != expected_path:
        raise ProjectTreeExtractionError(f"path mismatch for {name!r}")

    actual_index = _integer(raw.get("sibling_index"), f"sibling_index for {name!r}")
    if actual_index != sibling_index:
        raise ProjectTreeExtractionError(f"sibling_index mismatch for {name!r}")

    expected_locator = expected_parent_locator + (sibling_index,)
    locator_value = _list(raw.get("locator"), f"locator for {name!r}")
    locator = tuple(_integer(part, f"locator component for {name!r}") for part in locator_value)
    if locator != expected_locator:
        raise ProjectTreeExtractionError(f"locator mismatch for {name!r}")

    depth = _integer(raw.get("depth"), f"depth for {name!r}")
    if depth != len(expected_locator) - 1:
        raise ProjectTreeExtractionError(f"depth mismatch for {name!r}")
    if raw.get("truncated") is not False:
        raise ProjectTreeExtractionError(f"truncated tree node: {name!r}")

    raw_children = _list(raw.get("children"), f"children for {name!r}")
    children: list[_Node] = []
    count = 1
    for child_index, raw_child in enumerate(raw_children):
        child, child_count = _parse_node(
            raw_child,
            expected_parent_path=expected_path,
            expected_parent_locator=expected_locator,
            sibling_index=child_index,
        )
        children.append(child)
        count += child_count
    return _Node(name, path, locator, tuple(children)), count


def _locator_text(locator: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in locator)


def _record_id(locator: tuple[int, ...], kind: TextKind, text: str) -> str:
    material = f"{_locator_text(locator)}\x1f{kind.value}\x1f{text}".encode()
    return sha256(material).hexdigest()[:20]


def _make_record(
    node: _Node,
    kind: TextKind,
    *,
    source_name: str,
    context_label: str,
) -> InventoryRecord:
    locator = _locator_text(node.locator)
    record = InventoryRecord(
        record_id=_record_id(node.locator, kind, node.name),
        source_text=node.name,
        kind=kind,
        hierarchy=node.path[:-1] + (f"locator:{locator}",),
        context=f"{context_label}; locator={locator}",
        location=f"{source_name}#locator={locator}",
    )
    # Program names are identifiers and therefore always receive the shared
    # high-risk/review policy. Bookmark headings are explicitly classified as
    # comments here; their common /* ... */ wrapper must not be mistaken for a
    # filesystem path by the generic text-risk heuristic.
    return with_assessed_risk(record) if kind is TextKind.PROGRAM_NAME else record


def _program_children(node: _Node) -> tuple[_Node, _Node] | None:
    in_program_tree = (
        len(node.path) >= 3
        and node.path[0].startswith("程序:")
        and node.path[-2] in _PROGRAM_CONTAINERS
    )
    if (
        not in_program_tree
        or len(node.children) != 2
        or {child.name for child in node.children} != _PROGRAM_CHILDREN
    ):
        return None
    by_name = {child.name: child for child in node.children}
    return by_name["局部标号"], by_name["书签"]


def extract_project_tree_inventory(
    payload: object,
    *,
    source_name: str = "project-tree-inventory.json",
) -> list[InventoryRecord]:
    """Extract CJK program names and direct bookmark children from schema v1.

    The function refuses partial or structurally inconsistent input rather than
    silently producing an inventory that could be mistaken for complete.
    """

    root = _mapping(payload, "document")
    if root.get("schema_version") != 1:
        raise ProjectTreeExtractionError("schema_version must be 1")
    if root.get("action") != "inventory_project_tree":
        raise ProjectTreeExtractionError("action must be inventory_project_tree")
    inventory = _mapping(root.get("inventory"), "inventory")
    if inventory.get("complete") is not True:
        raise ProjectTreeExtractionError("inventory is incomplete")
    if inventory.get("truncated") is not False:
        raise ProjectTreeExtractionError("inventory is truncated")
    if inventory.get("restore_requested") is not True:
        raise ProjectTreeExtractionError("inventory did not request restoration")
    if inventory.get("restoration_complete") is not True:
        raise ProjectTreeExtractionError("inventory restoration is incomplete")
    warnings = _list(inventory.get("warnings"), "inventory warnings")
    if warnings:
        raise ProjectTreeExtractionError("inventory contains warnings")

    raw_roots = _list(inventory.get("roots"), "inventory roots")
    nodes: list[_Node] = []
    actual_count = 0
    for root_index, raw_node in enumerate(raw_roots):
        node, node_count = _parse_node(
            raw_node,
            expected_parent_path=(),
            expected_parent_locator=(),
            sibling_index=root_index,
        )
        nodes.append(node)
        actual_count += node_count
    declared_count = _integer(inventory.get("item_count"), "inventory item_count")
    if declared_count != actual_count:
        raise ProjectTreeExtractionError(
            f"item_count mismatch: declared {declared_count}, found {actual_count}"
        )

    records: list[InventoryRecord] = []

    def visit(node: _Node) -> None:
        program_children = _program_children(node)
        if program_children is not None:
            _, bookmarks = program_children
            if contains_cjk(node.name):
                records.append(
                    _make_record(
                        node,
                        TextKind.PROGRAM_NAME,
                        source_name=source_name,
                        context_label="project-tree program name",
                    )
                )
            for bookmark in bookmarks.children:
                if contains_cjk(bookmark.name):
                    records.append(
                        _make_record(
                            bookmark,
                            TextKind.COMMENT,
                            source_name=source_name,
                            context_label=f"bookmark in program {node.name}",
                        )
                    )
        for child in node.children:
            visit(child)

    for node in nodes:
        visit(node)
    return records
