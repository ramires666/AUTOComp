from __future__ import annotations

from dataclasses import dataclass

import pytest

from autocomp.worker.adapter import PywinautoKVStudioAdapter
from autocomp.worker.models import (
    ActionKind,
    action_request_from_payload,
)


@dataclass
class _Info:
    control_type: str
    automation_id: str = ""
    class_name: str = ""
    process_id: int = 77


class _Node:
    def __init__(self, name: str, *, children: tuple[_Node, ...] = ()) -> None:
        self.name = name
        self.state = 0 if children else 3
        self._children = children
        self.element_info = _Info("TreeItem")

    def window_text(self) -> str:
        return self.name

    def get_expand_state(self) -> int:
        return self.state

    def expand(self) -> None:
        self.state = 1

    def collapse(self) -> None:
        self.state = 0

    def children(self) -> list[_Node]:
        return list(self._children) if self.state in {1, 2} else []


class _Tree:
    def __init__(self, roots: tuple[_Node, ...]) -> None:
        self.roots = roots
        self.element_info = _Info(
            "Tree", automation_id="ProjectTreeView", class_name="SysTreeView32"
        )

    def children(self) -> list[_Node]:
        return list(self.roots)


class _Window:
    def __init__(self, tree: _Tree) -> None:
        self.tree = tree

    def window_text(self) -> str:
        return "Pilot Copy - KV STUDIO"

    def process_id(self) -> int:
        return 77

    def descendants(self, *, control_type: str) -> list[_Tree]:
        return [self.tree] if control_type == "Tree" else []

    def is_minimized(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def is_visible(self) -> bool:
        return False

    def restore(self) -> None:
        raise AssertionError("read-only status must not restore the window")

    def set_focus(self) -> None:
        raise AssertionError("read-only status must not focus the window")


class _Desktop:
    def __init__(self, *windows: object) -> None:
        self.windows_list = windows

    def windows(self) -> list[object]:
        return list(self.windows_list)


class _AuxiliaryWindow:
    def window_text(self) -> str:
        return "KV STUDIO - License"

    def process_id(self) -> int:
        return 77

    def descendants(self, *, control_type: str) -> list[object]:
        assert control_type == "Tree"
        return []

    def is_minimized(self) -> bool:
        return False

    def is_enabled(self) -> bool:
        return True

    def is_visible(self) -> bool:
        return True


class _FragileStateWindow(_Window):
    def is_enabled(self) -> bool:
        raise RuntimeError("stale UIA property")

    def is_visible(self) -> bool:
        raise RuntimeError("stale UIA property")


class _StatusAdapter(PywinautoKVStudioAdapter):
    def __init__(self, *windows: object) -> None:
        super().__init__(expansion_settle_seconds=0)
        self.windows = windows

    def _desktop(self) -> _Desktop:
        return _Desktop(*self.windows)


class _DirectEditAdapter(PywinautoKVStudioAdapter):
    def __init__(self, root: _Node, *, maximum: int | None = None) -> None:
        super().__init__(expansion_settle_seconds=0)
        self.window = _Window(_Tree((root,)))
        self.maximum = maximum

    def _desktop(self) -> _Desktop:
        return _Desktop(self.window)

    def _restore_and_focus_editor(self, window: object) -> None:
        assert window is self.window

    def _commit_tree_item_text(
        self, window: object, tree: object, item: _Node, target: str
    ) -> None:
        assert window is self.window
        assert tree is self.window.tree
        item.name = target if self.maximum is None else target[: self.maximum]


def test_exact_payload_parser_rejects_extra_fields_and_wrong_types() -> None:
    valid = {
        "action": "rename_tree_item",
        "checkpoint": "pilot_01",
        "locator": [0, 2],
        "expected_path": ["Programs", "中文"],
        "expected_source": "中文",
        "target": "English",
        "apply": True,
    }

    request = action_request_from_payload(valid)

    assert request.kind is ActionKind.RENAME_TREE_ITEM
    assert request.locator == (0, 2)
    with pytest.raises(ValueError, match="missing or unexpected"):
        action_request_from_payload({**valid, "keys": "{F4}"})
    with pytest.raises(ValueError, match="locator"):
        action_request_from_payload({**valid, "locator": [True]})


def test_adapter_status_does_not_restore_or_focus_minimized_kv_window() -> None:
    adapter = _DirectEditAdapter(_Node("Programs"))

    status = adapter.status()

    assert status.title == "Pilot Copy - KV STUDIO"
    assert status.process_id == 77
    assert status.minimized is True
    assert status.project_tree_available is True


def test_status_selects_unique_project_editor_and_ignores_auxiliary_kv_dialog() -> None:
    editor = _Window(_Tree((_Node("Programs"),)))
    adapter = _StatusAdapter(_AuxiliaryWindow(), editor)

    status = adapter.status()

    assert status.title == "Pilot Copy - KV STUDIO"
    assert status.project_tree_available is True


def test_status_survives_unavailable_secondary_uia_state_properties() -> None:
    editor = _FragileStateWindow(_Tree((_Node("Programs"),)))
    adapter = _StatusAdapter(editor)

    status = adapter.status()

    assert status.project_tree_available is True
    assert status.enabled is False
    assert status.visible is False


def test_configured_title_pattern_cannot_broaden_allowlist_to_other_apps() -> None:
    adapter = PywinautoKVStudioAdapter(title_pattern=r".*")

    assert adapter._is_allowed_title("Pilot Copy - KV STUDIO") is True
    assert adapter._is_allowed_title("Windows PowerShell") is False


def test_edge_title_with_zero_width_format_character_is_not_allowlisted() -> None:
    adapter = PywinautoKVStudioAdapter()
    edge_title = "KV STUDIO 用户支持 | Keyence — Профиль 1: Microsoft\u200b Edge"

    assert adapter._is_allowed_title(edge_title) is False


def test_adapter_renames_only_when_full_indexed_path_matches() -> None:
    child = _Node("中文")
    root = _Node("Programs", children=(child,))
    adapter = _DirectEditAdapter(root)

    result = adapter.rename_tree_item(
        locator=(0, 0),
        expected_path=("Wrong root", "中文"),
        expected_source="中文",
        target="English",
    )

    assert result.performed is False
    assert child.name == "中文"
    assert "identity changed" in result.error
    assert root.state == 0


def test_adapter_rolls_back_kv_truncated_target_to_exact_source() -> None:
    child = _Node("中文")
    root = _Node("Programs", children=(child,))
    adapter = _DirectEditAdapter(root, maximum=7)

    result = adapter.rename_tree_item(
        locator=(0, 0),
        expected_path=("Programs", "中文"),
        expected_source="中文",
        target="English Candidate",
    )

    assert result.performed is False
    assert result.rollback_attempted is True
    assert result.rollback_succeeded is True
    assert result.after == "中文"
    assert child.name == "中文"
    assert root.state == 0
