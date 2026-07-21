from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from autocomp.worker.adapter import PywinautoKVStudioAdapter


@dataclass
class _Info:
    handle: int
    process_id: int = 77
    class_name: str = "WindowsForms10.SysTreeView32.app.0.33c0d9d"


class _Window:
    def process_id(self) -> int:
        return 77


class _Tree:
    def __init__(self) -> None:
        self.element_info = _Info(handle=42)

    def descendants(self, **_: object) -> list[object]:
        raise AssertionError("rename must not scan UIA Edit descendants")


class _Item:
    def __init__(self) -> None:
        self.selected = False

    def select(self) -> None:
        self.selected = True


@dataclass
class _Parent:
    handle: int


class _NativeTree:
    def __init__(self, *, active_handle: int = 0) -> None:
        self.handle = 42
        self.active_handle = active_handle
        self.actions: list[str] = []
        self.messages: list[tuple[int, int, int]] = []

    def process_id(self) -> int:
        return 77

    def set_focus(self) -> None:
        self.actions.append("focus")

    def type_keys(self, key: str, *, set_foreground: bool) -> None:
        assert set_foreground is False
        self.actions.append(key)
        if key == "{F2}":
            self.active_handle = 501

    def send_message(self, message: int, wparam: int, lparam: int) -> int:
        self.messages.append((message, wparam, lparam))
        return self.active_handle


class _NativeEdit:
    def __init__(
        self,
        tree: _NativeTree,
        *,
        process_id: int = 77,
        parent_handle: int = 42,
        class_name: str = "Edit",
        visible: bool = True,
        enabled: bool = True,
        transform: Callable[[str], str] = lambda value: value,
    ) -> None:
        self.handle = 501
        self._tree = tree
        self._process_id = process_id
        self._parent = _Parent(parent_handle)
        self._class_name = class_name
        self._visible = visible
        self._enabled = enabled
        self._transform = transform
        self.text = "source"
        self.actions: list[str] = []

    def process_id(self) -> int:
        return self._process_id

    def parent(self) -> _Parent:
        return self._parent

    def class_name(self) -> str:
        return self._class_name

    def is_visible(self) -> bool:
        return self._visible

    def is_enabled(self) -> bool:
        return self._enabled

    def set_focus(self) -> None:
        self.actions.append("focus")

    def set_edit_text(self, value: str) -> None:
        self.actions.append(f"set:{value}")
        self.text = self._transform(value)

    def window_text(self) -> str:
        return self.text

    def type_keys(self, key: str, *, set_foreground: bool) -> None:
        assert set_foreground is False
        self.actions.append(key)
        if key in {"{ENTER}", "{ESC}"}:
            self._tree.active_handle = 0


class _NativeAdapter(PywinautoKVStudioAdapter):
    def __init__(self, native_tree: _NativeTree, native_edit: _NativeEdit) -> None:
        super().__init__(expansion_settle_seconds=0)
        self.native_tree = native_tree
        self.native_edit = native_edit

    def _native_tree_wrapper(self, handle: int) -> object:
        if handle != self.native_tree.handle:
            raise AssertionError(f"unexpected native tree handle: {handle}")
        return self.native_tree

    def _native_edit_wrapper(self, handle: int) -> object:
        if handle != self.native_edit.handle:
            raise AssertionError(f"unexpected native edit handle: {handle}")
        return self.native_edit


def _fixture(**edit_options: object) -> tuple[_NativeAdapter, _NativeTree, _NativeEdit]:
    native_tree = _NativeTree()
    native_edit = _NativeEdit(native_tree, **edit_options)
    return _NativeAdapter(native_tree, native_edit), native_tree, native_edit


def test_native_tree_is_focused_before_f2_and_exact_edit_is_committed() -> None:
    adapter, native_tree, native_edit = _fixture()
    item = _Item()

    adapter._commit_tree_item_text(_Window(), _Tree(), item, "English")

    assert item.selected is True
    assert native_tree.actions == ["focus", "focus", "{F2}"]
    assert native_edit.actions == ["focus", "set:English", "{ENTER}"]
    assert native_edit.text == "English"
    assert native_tree.active_handle == 0
    assert native_tree.messages


def test_preexisting_native_editor_fails_closed_without_keys_or_selection() -> None:
    native_tree = _NativeTree(active_handle=501)
    native_edit = _NativeEdit(native_tree)
    adapter = _NativeAdapter(native_tree, native_edit)
    item = _Item()

    with pytest.raises(RuntimeError, match="already has an active editor"):
        adapter._commit_tree_item_text(_Window(), _Tree(), item, "English")

    assert item.selected is False
    assert native_tree.actions == []
    assert native_edit.actions == []


@pytest.mark.parametrize(
    ("edit_options", "error"),
    [
        ({"process_id": 88}, "process identity"),
        ({"parent_handle": 99}, "not a child"),
        ({"class_name": "TextBox"}, "unsupported window class"),
        ({"visible": False}, "not ready"),
        ({"enabled": False}, "not ready"),
    ],
)
def test_untrusted_native_editor_is_rejected_without_text_or_key_input(
    edit_options: dict[str, object], error: str
) -> None:
    adapter, native_tree, native_edit = _fixture(**edit_options)

    with pytest.raises(RuntimeError, match=error):
        adapter._commit_tree_item_text(_Window(), _Tree(), _Item(), "English")

    assert native_tree.actions == ["focus", "focus", "{F2}"]
    assert native_edit.actions == []
    assert native_tree.active_handle == 501


def test_non_exact_edit_text_is_cancelled_before_enter() -> None:
    adapter, native_tree, native_edit = _fixture(transform=lambda value: value[:4])

    with pytest.raises(RuntimeError, match="did not accept the exact target"):
        adapter._commit_tree_item_text(_Window(), _Tree(), _Item(), "English")

    assert native_edit.actions == ["focus", "set:English", "{ESC}"]
    assert "{ENTER}" not in native_edit.actions
    assert native_tree.active_handle == 0


def test_tree_rename_key_allowlist_rejects_arbitrary_input() -> None:
    adapter, native_tree, _ = _fixture()

    with pytest.raises(RuntimeError, match="non-allowlisted"):
        adapter._send_tree_rename_key(native_tree, "{F4}")

    assert native_tree.actions == []
