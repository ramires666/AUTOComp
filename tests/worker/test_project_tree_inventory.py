from __future__ import annotations

from dataclasses import dataclass

from autocomp.worker.adapter import PywinautoKVStudioAdapter


@dataclass
class _ElementInfo:
    control_type: str
    automation_id: str = ""
    class_name: str = ""
    process_id: int = 101


class _TreeNode:
    def __init__(
        self,
        name: str,
        *,
        state: int,
        children: tuple[_TreeNode, ...] = (),
        fail_visible: bool = False,
        fail_children: bool = False,
    ) -> None:
        self.name = name
        self.state = state
        self._children = children
        self.fail_visible = fail_visible
        self.fail_children = fail_children
        self.element_info = _ElementInfo("TreeItem")
        self.actions: list[str] = []

    def window_text(self) -> str:
        return self.name

    def get_expand_state(self) -> int:
        return self.state

    def expand(self) -> None:
        self.actions.append("expand")
        self.state = 1

    def collapse(self) -> None:
        self.actions.append("collapse")
        self.state = 0

    def children(self) -> list[_TreeNode]:
        # Model a native lazy tree: descendants are materialized only while open.
        if self.fail_children:
            raise RuntimeError("simulated child enumeration failure")
        return list(self._children) if self.state in {1, 2} else []

    def is_visible(self) -> bool:
        if self.fail_visible:
            raise RuntimeError("simulated disappearing UIA node")
        return True


class _ProjectTree:
    def __init__(self, roots: tuple[_TreeNode, ...]) -> None:
        self._roots = roots
        self.element_info = _ElementInfo(
            "Tree", automation_id="ProjectTreeView", class_name="SysTreeView32"
        )

    def children(self) -> list[_TreeNode]:
        return list(self._roots)


class _Window:
    def __init__(self, tree: _ProjectTree) -> None:
        self._tree = tree

    def window_text(self) -> str:
        return "Example - KV STUDIO"

    def process_id(self) -> int:
        return 101

    def descendants(self, *, control_type: str) -> list[_ProjectTree]:
        assert control_type == "Tree"
        return [self._tree]


class _Desktop:
    def __init__(self, window: _Window) -> None:
        self._window = window

    def windows(self) -> list[_Window]:
        return [self._window]


class _TestAdapter(PywinautoKVStudioAdapter):
    def __init__(self, roots: tuple[_TreeNode, ...]) -> None:
        super().__init__(expansion_settle_seconds=0)
        self.desktop = _Desktop(_Window(_ProjectTree(roots)))

    def _desktop(self) -> _Desktop:
        return self.desktop


class _StaleAfterExpandNode(_TreeNode):
    def collapse(self) -> None:
        raise RuntimeError("stale UIA wrapper")


class _SwitchingAdapter(PywinautoKVStudioAdapter):
    def __init__(self, first: _TreeNode, replacement: _TreeNode) -> None:
        super().__init__(expansion_settle_seconds=0)
        self._desktops = (
            _Desktop(_Window(_ProjectTree((first,)))),
            _Desktop(_Window(_ProjectTree((replacement,)))),
        )
        self._desktop_calls = 0

    def _desktop(self) -> _Desktop:
        index = min(self._desktop_calls, len(self._desktops) - 1)
        self._desktop_calls += 1
        return self._desktops[index]


def test_expand_all_discovers_lazy_collapsed_children_and_restores_parent() -> None:
    lazy_child = _TreeNode("PartsLife", state=3)
    programs = _TreeNode("Programs", state=0, children=(lazy_child,))
    adapter = _TestAdapter((programs,))

    inventory = adapter.inventory_project_tree(expand_all=True, restore_state=True)

    root = inventory.roots[0]
    assert root.initial_expansion_state == "collapsed"
    assert root.expanded_for_inventory is True
    assert [child.name for child in root.children] == ["PartsLife"]
    assert root.children[0].path == ("Programs", "PartsLife")
    assert inventory.item_count == 2
    assert inventory.expanded_count == 1
    assert inventory.restored_count == 1
    assert inventory.restoration_complete is True
    assert programs.state == 0
    assert programs.actions == ["expand", "collapse"]


def test_expanded_nodes_are_restored_when_snapshotting_fails() -> None:
    programs = _TreeNode("Programs", state=0, fail_visible=True)
    adapter = _TestAdapter((programs,))

    inventory = adapter.inventory_project_tree(expand_all=True, restore_state=True)

    assert inventory.complete is False
    assert any("traversal failed" in warning for warning in inventory.warnings)
    assert inventory.restoration_complete is True
    assert programs.state == 0
    assert programs.actions == ["expand", "collapse"]


def test_nodes_expanded_before_inventory_remain_expanded() -> None:
    child = _TreeNode("Main", state=3)
    programs = _TreeNode("Programs", state=1, children=(child,))
    adapter = _TestAdapter((programs,))

    inventory = adapter.inventory_project_tree(expand_all=True, restore_state=True)

    assert [node.name for node in inventory.roots[0].children] == ["Main"]
    assert inventory.roots[0].initial_expansion_state == "expanded"
    assert inventory.roots[0].expanded_for_inventory is False
    assert inventory.expanded_count == 0
    assert inventory.restored_count == 0
    assert programs.state == 1
    assert programs.actions == []


def test_snapshot_only_never_expands_or_collapses_nodes() -> None:
    lazy_child = _TreeNode("Hidden until expanded", state=3)
    programs = _TreeNode("Programs", state=0, children=(lazy_child,))
    adapter = _TestAdapter((programs,))

    inventory = adapter.inventory_project_tree(expand_all=False, restore_state=True)

    assert [node.name for node in inventory.roots] == ["Programs"]
    assert inventory.roots[0].children == ()
    assert inventory.roots[0].expanded_for_inventory is False
    assert inventory.expanded_count == 0
    assert inventory.restored_count == 0
    assert programs.state == 0
    assert programs.actions == []


def test_child_enumeration_failure_cannot_be_reported_as_complete() -> None:
    programs = _TreeNode("Programs", state=1, fail_children=True)
    adapter = _TestAdapter((programs,))

    inventory = adapter.inventory_project_tree(expand_all=False, restore_state=True)

    assert inventory.complete is False
    assert inventory.truncated is True
    assert inventory.warnings


def test_partial_expansion_cannot_be_reported_as_complete() -> None:
    child = _TreeNode("Main", state=3)
    programs = _TreeNode("Programs", state=2, children=(child,))
    adapter = _TestAdapter((programs,))

    inventory = adapter.inventory_project_tree(expand_all=True, restore_state=True)

    assert inventory.complete is False
    assert inventory.truncated is True
    assert any("partially expanded" in warning for warning in inventory.warnings)
    assert programs.state == 2
    assert programs.actions == []


def test_restoration_cannot_be_disabled() -> None:
    adapter = _TestAdapter((_TreeNode("Programs", state=3),))

    try:
        adapter.inventory_project_tree(expand_all=True, restore_state=False)
    except ValueError as exc:
        assert "must restore" in str(exc)
    else:
        raise AssertionError("restore_state=False must be rejected")


def test_stale_wrapper_is_re_resolved_by_indexed_locator_for_restoration() -> None:
    stale = _StaleAfterExpandNode(
        "Programs", state=0, children=(_TreeNode("Main", state=3),)
    )
    replacement = _TreeNode(
        "Programs", state=1, children=(_TreeNode("Main", state=3),)
    )
    adapter = _SwitchingAdapter(stale, replacement)

    inventory = adapter.inventory_project_tree(expand_all=True, restore_state=True)

    assert inventory.restoration_complete is True
    assert inventory.restored_count == 1
    assert replacement.state == 0
    assert inventory.roots[0].locator == (0,)
    assert inventory.roots[0].children[0].locator == (0, 0)
