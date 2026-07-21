from __future__ import annotations

import pytest

from autocomp.worker.adapter import FakeKVStudioAdapter, PywinautoKVStudioAdapter
from autocomp.worker.models import ActionKind, ActionRequest, action_request_from_payload
from autocomp.worker.service import KVStudioWorker


def _request(*, apply: bool) -> ActionRequest:
    return ActionRequest(
        ActionKind.ACTIVATE_TREE_ITEM,
        checkpoint="bookmark_01" if apply else "",
        locator=(0, 0),
        expected_path=("Programs", "/*报警*/"),
        expected_source="/*报警*/",
        apply=apply,
    )


def test_activate_payload_requires_exact_tree_precondition() -> None:
    payload = {
        "action": "activate_tree_item",
        "checkpoint": "bookmark_01",
        "locator": [0, 0],
        "expected_path": ["Programs", "/*报警*/"],
        "expected_source": "/*报警*/",
        "apply": True,
    }

    request = action_request_from_payload(payload)

    assert request.kind is ActionKind.ACTIVATE_TREE_ITEM
    assert request.locator == (0, 0)
    with pytest.raises(ValueError, match="missing or unexpected"):
        action_request_from_payload({**payload, "x": 10})


def test_activate_dry_run_validates_without_touching_adapter() -> None:
    adapter = FakeKVStudioAdapter()

    result = KVStudioWorker(adapter).execute(_request(apply=False))

    assert result.performed is False
    assert result.audit == {"mode": "dry-run", "operation": "activate_tree_item"}
    assert adapter.activation_calls == []


def test_activate_apply_uses_exact_precondition_and_checkpoint() -> None:
    adapter = FakeKVStudioAdapter()
    adapter.tree_items[(0, 0)] = ("Programs", "/*报警*/")

    result = KVStudioWorker(adapter, apply_enabled=True).execute(_request(apply=True))

    assert result.performed is True
    assert result.audit["checkpoint"] == "bookmark_01"
    assert result.visual_snapshot is not None
    assert adapter.activation_calls == [
        {
            "locator": (0, 0),
            "expected_path": ("Programs", "/*报警*/"),
            "expected_source": "/*报警*/",
        }
    ]


def test_activate_apply_requires_named_checkpoint() -> None:
    adapter = FakeKVStudioAdapter()
    adapter.tree_items[(0, 0)] = ("Programs", "/*报警*/")
    request = ActionRequest(
        ActionKind.ACTIVATE_TREE_ITEM,
        locator=(0, 0),
        expected_path=("Programs", "/*报警*/"),
        expected_source="/*报警*/",
        apply=True,
    )

    with pytest.raises(ValueError, match="checkpoint"):
        KVStudioWorker(adapter, apply_enabled=True).execute(request)

    assert adapter.activation_calls == []


def test_activate_preserves_success_when_followup_snapshot_fails() -> None:
    class NoSnapshotAdapter(FakeKVStudioAdapter):
        def visual_snapshot(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("capture unavailable")

    adapter = NoSnapshotAdapter()
    adapter.tree_items[(0, 0)] = ("Programs", "/*报警*/")

    result = KVStudioWorker(adapter, apply_enabled=True).execute(_request(apply=True))

    assert result.performed is True
    assert result.visual_snapshot is None
    assert result.audit["snapshot"] == "unavailable"


class _Info:
    control_type = "TreeItem"


class _Node:
    def __init__(self, name: str, *, children: tuple[_Node, ...] = ()) -> None:
        self.name = name
        self._children = children
        self.expanded = not children
        self.selected = False
        self.visible = False
        self.double_clicked = False
        self.element_info = _Info()

    def window_text(self) -> str:
        return self.name

    def get_expand_state(self) -> int:
        return 1 if self.expanded else 0

    def expand(self) -> None:
        self.expanded = True

    def children(self) -> list[_Node]:
        return list(self._children) if self.expanded else []

    def ensure_visible(self) -> None:
        self.visible = True

    def select(self) -> None:
        self.selected = True

    def double_click_input(self, *, button: str) -> None:
        assert button == "left"
        self.double_clicked = True


class _Tree:
    def __init__(self, root: _Node) -> None:
        self.root = root

    def children(self) -> list[_Node]:
        return [self.root]


class _Window:
    def window_text(self) -> str:
        return "Pilot Copy - KV STUDIO"

    def process_id(self) -> int:
        return 77


class _ActivationAdapter(PywinautoKVStudioAdapter):
    def __init__(self, root: _Node) -> None:
        super().__init__(expansion_settle_seconds=0)
        self.window = _Window()
        self.tree = _Tree(root)

    def _find_project_tree(self) -> tuple[_Window, _Tree]:
        return self.window, self.tree

    def _restore_and_focus_editor(self, window: object) -> None:
        assert window is self.window


def test_windows_activation_resolves_full_path_before_double_click() -> None:
    bookmark = _Node("/*报警*/")
    root = _Node("Programs", children=(bookmark,))
    adapter = _ActivationAdapter(root)

    performed = adapter.activate_tree_item(
        locator=(0, 0),
        expected_path=("Programs", "/*报警*/"),
        expected_source="/*报警*/",
    )

    assert performed is True
    assert bookmark.visible is True
    assert bookmark.selected is True
    assert bookmark.double_clicked is True


def test_windows_activation_rejects_stale_path_without_input() -> None:
    bookmark = _Node("/*报警*/")
    adapter = _ActivationAdapter(_Node("Programs", children=(bookmark,)))

    with pytest.raises(RuntimeError, match="identity changed"):
        adapter.activate_tree_item(
            locator=(0, 0),
            expected_path=("Programs", "/*Other*/"),
            expected_source="/*Other*/",
        )

    assert bookmark.selected is False
    assert bookmark.double_clicked is False
