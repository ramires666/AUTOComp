from __future__ import annotations

from dataclasses import dataclass

import pytest
from PIL import Image

from autocomp.desktop import DesktopInputOperation, UniversalDesktopAdapter


@dataclass
class _Rect:
    left: int
    top: int
    right: int
    bottom: int


class _Window:
    def __init__(
        self,
        handle: int,
        title: str,
        process_id: int,
        bounds: tuple[int, int, int, int],
    ) -> None:
        self.handle = handle
        self.title = title
        self.pid = process_id
        self.bounds = bounds
        self.visible = True
        self.enabled = True
        self.minimized = False
        self.class_name_value = "Window"
        self.owner: _Window = self
        self.calls: list[tuple[object, ...]] = []

    def window_text(self) -> str:
        return self.title

    def process_id(self) -> int:
        return self.pid

    def rectangle(self) -> _Rect:
        return _Rect(*self.bounds)

    def is_visible(self) -> bool:
        return self.visible

    def is_enabled(self) -> bool:
        return self.enabled

    def is_minimized(self) -> bool:
        return self.minimized

    def top_level_parent(self) -> _Window:
        return self.owner

    def class_name(self) -> str:
        return self.class_name_value

    def set_focus(self) -> None:
        self.calls.append(("focus",))

    def click_input(self, **kwargs: object) -> None:
        self.calls.append(("click_input", kwargs))

    def double_click_input(self, **kwargs: object) -> None:
        self.calls.append(("double_click_input", kwargs))

    def wheel_mouse_input(self, **kwargs: object) -> None:
        self.calls.append(("wheel_mouse_input", kwargs))

    def type_keys(self, key: str, *, set_foreground: bool) -> None:
        self.calls.append(("type_keys", key, set_foreground))


class _Specification:
    def __init__(self, window: _Window) -> None:
        self.window = window

    def wrapper_object(self) -> _Window:
        return self.window


class _Desktop:
    def __init__(self, *windows: _Window) -> None:
        self.windows_by_handle = {window.handle: window for window in windows}

    def windows(self, *, top_level_only: bool = False, **_: object) -> list[_Window]:
        windows = list(self.windows_by_handle.values())
        if top_level_only:
            return [
                window
                for window in windows
                if window.top_level_parent().handle == window.handle
            ]
        return windows

    def window(self, *, handle: int) -> _Specification:
        return _Specification(self.windows_by_handle[handle])


class _Adapter(UniversalDesktopAdapter):
    def __init__(self, *windows: _Window) -> None:
        self.desktop = _Desktop(*windows)
        self.foreground_handle = next(iter(self.desktop.windows_by_handle))
        self.native_owners: dict[int, int] = {}

    def _desktop(self) -> _Desktop:
        return self.desktop

    def _grab_bbox(self, bounds: tuple[int, int, int, int]) -> Image.Image:
        return Image.new("RGB", (bounds[2] - bounds[0], bounds[3] - bounds[1]), "white")

    def _foreground_window_handle(self) -> int:
        return self.foreground_handle

    def _native_owner_handle(self, handle: int) -> int:
        return self.native_owners.get(handle, 0)

    def _window_process_id(self, handle: int) -> int:
        return self.desktop.windows_by_handle[handle].pid


def test_enumerates_visible_top_level_windows_without_product_allowlist() -> None:
    kv = _Window(101, "KV STUDIO", 11, (10, 20, 310, 220))
    schneider = _Window(202, "EcoStruxure", 22, (-500, 0, 0, 400))
    adapter = _Adapter(kv, schneider)

    windows = adapter.enumerate_windows()

    assert [(item.handle, item.title) for item in windows] == [
        (101, "KV STUDIO"),
        (202, "EcoStruxure"),
    ]
    assert windows[1].bounds == (-500, 0, 0, 400)


def test_enumerates_and_accepts_visible_owned_native_dialog() -> None:
    main = _Window(101, "KV STUDIO", 11, (10, 20, 800, 600))
    dialog = _Window(102, "Program Properties", 11, (100, 100, 500, 400))
    dialog.owner = main
    dialog.class_name_value = "#32770"
    adapter = _Adapter(main, dialog)

    windows = adapter.enumerate_windows()
    assert [(item.handle, item.title) for item in windows] == [
        (101, "KV STUDIO"),
        (102, "Program Properties"),
    ]

    adapter.input(
        handle=102,
        expected_pid=11,
        expected_title="Program Properties",
        operation="key_enter",
    )
    assert dialog.calls[-1] == ("type_keys", "{ENTER}", False)


def test_accepts_native_owner_as_foreground_for_top_level_modal() -> None:
    main = _Window(101, "KV STUDIO", 11, (10, 20, 800, 600))
    dialog = _Window(102, "Program Properties", 11, (100, 100, 500, 400))
    dialog.class_name_value = "#32770"
    adapter = _Adapter(main, dialog)
    adapter.foreground_handle = main.handle
    adapter.native_owners[dialog.handle] = main.handle

    adapter.input(
        handle=dialog.handle,
        expected_pid=dialog.pid,
        expected_title=dialog.title,
        operation="key_enter",
    )

    assert dialog.calls[-1] == ("type_keys", "{ENTER}", False)
    assert ("focus",) not in dialog.calls


def test_rejects_non_dialog_child_window() -> None:
    main = _Window(101, "KV STUDIO", 11, (10, 20, 800, 600))
    child = _Window(102, "Edit", 11, (100, 100, 500, 140))
    child.owner = main
    child.class_name_value = "Edit"
    adapter = _Adapter(main, child)

    with pytest.raises(RuntimeError, match="owned dialog"):
        adapter.snapshot(handle=102, expected_pid=11, expected_title="Edit")


def test_snapshot_requires_exact_handle_pid_title_and_returns_png_hash() -> None:
    window = _Window(101, "Any App", 11, (10, 20, 110, 70))
    adapter = _Adapter(window)

    frame = adapter.snapshot(handle=101, expected_pid=11, expected_title="Any App")

    assert (frame.width, frame.height) == (100, 50)
    assert frame.mime_type == "image/png"
    assert len(frame.png_sha256) == 64
    assert frame.png_base64.startswith("iVBOR")
    with pytest.raises(RuntimeError, match="identity precondition"):
        adapter.snapshot(handle=101, expected_pid=99, expected_title="Any App")


def test_pointer_coordinates_are_relative_and_rejected_outside_frame() -> None:
    window = _Window(101, "Any App", 11, (10, 20, 110, 70))
    adapter = _Adapter(window)

    adapter.input(
        handle=101,
        expected_pid=11,
        expected_title="Any App",
        operation="right",
        x=5,
        y=6,
    )

    assert window.calls[-1] == (
        "click_input",
        {"button": "right", "coords": (5, 6), "absolute": False},
    )
    with pytest.raises(ValueError, match="outside"):
        adapter.input(
            handle=101,
            expected_pid=11,
            expected_title="Any App",
            operation="click",
            x=100,
            y=0,
        )


def test_double_click_and_wheel_use_supported_wrapper_methods() -> None:
    window = _Window(101, "Any App", 11, (0, 0, 100, 100))
    adapter = _Adapter(window)

    adapter.input(
        handle=101,
        expected_pid=11,
        expected_title="Any App",
        operation="double",
        x=10,
        y=20,
    )
    adapter.input(
        handle=101,
        expected_pid=11,
        expected_title="Any App",
        operation="wheel",
        x=30,
        y=40,
        delta=-3,
    )

    assert ("double_click_input", {"button": "left", "coords": (10, 20)}) in window.calls
    assert ("wheel_mouse_input", {"wheel_dist": -3, "coords": (30, 40)}) in window.calls


@pytest.mark.parametrize(
    ("operation", "encoded"),
    [
        (DesktopInputOperation.KEY_ENTER, "{ENTER}"),
        (DesktopInputOperation.KEY_ESCAPE, "{ESC}"),
        (DesktopInputOperation.KEY_CTRL_A, "^a"),
        (DesktopInputOperation.KEY_F2, "{F2}"),
        (DesktopInputOperation.TAB, "{TAB}"),
        (DesktopInputOperation.SHIFT_TAB, "+{TAB}"),
    ],
)
def test_keyboard_operations_are_a_fixed_enum(
    operation: DesktopInputOperation, encoded: str
) -> None:
    window = _Window(101, "Any App", 11, (0, 0, 100, 100))
    adapter = _Adapter(window)

    adapter.input(
        handle=101,
        expected_pid=11,
        expected_title="Any App",
        operation=operation,
    )

    assert window.calls[-1] == ("type_keys", encoded, False)


def test_unknown_operation_and_stale_identity_fail_before_input() -> None:
    window = _Window(101, "Any App", 11, (0, 0, 100, 100))
    adapter = _Adapter(window)

    with pytest.raises(ValueError, match="unsupported"):
        adapter.input(
            handle=101,
            expected_pid=11,
            expected_title="Any App",
            operation="delete",
        )
    with pytest.raises(RuntimeError, match="identity precondition"):
        adapter.input(
            handle=101,
            expected_pid=11,
            expected_title="Wrong",
            operation="key_enter",
        )
    assert window.calls == []
