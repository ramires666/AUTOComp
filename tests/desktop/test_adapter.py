from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest
from PIL import Image

from autocomp.desktop import (
    DesktopClipboardFormat,
    DesktopClipboardSnapshot,
    DesktopInputOperation,
    UniversalDesktopAdapter,
)
from autocomp.desktop import adapter as adapter_module


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
        self.focused_handle = self.foreground_handle
        self.native_owners: dict[int, int] = {}
        self.sent_keys: list[str] = []
        self.clipboard_value = ""

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

    def _focused_window_handle(self) -> int:
        return self.focused_handle

    def _send_keys(self, keys: str) -> None:
        self.sent_keys.append(keys)

    def _unicode_clipboard_text(self) -> str:
        return self.clipboard_value


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
    adapter.native_owners[dialog.handle] = main.handle

    windows = adapter.enumerate_windows()
    assert [(item.handle, item.title) for item in windows] == [
        (101, "KV STUDIO"),
        (102, "Program Properties"),
    ]
    assert windows[1].owner_handle == main.handle
    assert windows[1].enabled is True
    assert windows[1].class_name == "#32770"

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


def test_owned_window_discovery_does_not_depend_on_window_class() -> None:
    main = _Window(101, "Any App", 11, (10, 20, 800, 600))
    popup = _Window(102, "Custom Toolkit Popup", 11, (100, 100, 500, 400))
    popup.owner = main
    popup.class_name_value = "VendorSpecificPopup42"
    adapter = _Adapter(main, popup)
    adapter.native_owners[popup.handle] = main.handle
    adapter.foreground_handle = popup.handle

    windows = adapter.enumerate_windows()

    assert [item.handle for item in windows] == [main.handle, popup.handle]
    assert windows[1].foreground is True
    assert windows[1].class_name == "VendorSpecificPopup42"


def test_atomic_sequence_keeps_keyboard_on_clicked_child() -> None:
    main = _Window(101, "Any App", 11, (0, 0, 800, 600))
    dialog = _Window(102, "Properties", 11, (100, 100, 500, 400))
    dialog.owner = main
    adapter = _Adapter(main, dialog)
    adapter.native_owners[dialog.handle] = main.handle

    completed = adapter.input_sequence(
        handle=dialog.handle,
        expected_pid=dialog.pid,
        expected_title=dialog.title,
        operations=(
            {"operation": "click", "x": 100, "y": 150},
            {"operation": "key_ctrl_a"},
        ),
    )

    assert completed == 2
    assert adapter.sent_keys == ["^a"]


def test_atomic_sequence_stops_if_keyboard_focus_leaves_selected_process() -> None:
    app = _Window(101, "Any App", 11, (0, 0, 800, 600))
    other = _Window(202, "Other App", 22, (0, 0, 800, 600))
    adapter = _Adapter(app, other)
    adapter.focused_handle = other.handle

    with pytest.raises(RuntimeError, match="keyboard focus left"):
        adapter.input_sequence(
            handle=app.handle,
            expected_pid=app.pid,
            expected_title=app.title,
            operations=(
                {"operation": "click", "x": 100, "y": 150},
                {"operation": "key_ctrl_a"},
            ),
        )


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


def test_snapshot_prefers_print_window_for_occluded_content() -> None:
    window = _Window(101, "Occluded App", 11, (10, 20, 110, 70))
    adapter = _Adapter(window)
    rendered = Image.new("RGB", (100, 50), "white")
    rendered.putpixel((0, 0), (0, 0, 0))
    calls: list[str] = []
    adapter._print_window = lambda handle, size: (  # type: ignore[method-assign]
        calls.append(f"print:{handle}:{size}") or rendered
    )
    adapter._grab_bbox = lambda bounds: (  # type: ignore[method-assign]
        calls.append(f"screen:{bounds}") or Image.new("RGB", (100, 50), "red")
    )

    frame = adapter.snapshot(
        handle=window.handle,
        expected_pid=window.pid,
        expected_title=window.title,
    )

    assert (frame.width, frame.height) == (100, 50)
    assert calls == ["print:101:(100, 50)"]


@pytest.mark.parametrize("bad_size", [(100, 50), (90, 50)])
def test_snapshot_falls_back_when_print_window_is_blank_or_wrong_size(
    bad_size: tuple[int, int],
) -> None:
    window = _Window(101, "Any App", 11, (10, 20, 110, 70))
    adapter = _Adapter(window)
    calls: list[str] = []
    adapter._print_window = lambda handle, size: Image.new(  # type: ignore[method-assign]
        "RGB", bad_size, "black" if bad_size == size else "red"
    )
    adapter._grab_bbox = lambda bounds: (  # type: ignore[method-assign]
        calls.append("screen") or Image.new("RGB", (100, 50), "white")
    )

    frame = adapter.snapshot(
        handle=window.handle,
        expected_pid=window.pid,
        expected_title=window.title,
    )

    assert (frame.width, frame.height) == (100, 50)
    assert calls == ["screen"]


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
        (DesktopInputOperation.KEY_CTRL_C, "^c"),
        (DesktopInputOperation.KEY_CTRL_D, "^d"),
        (DesktopInputOperation.KEY_CTRL_DOWN, "^{DOWN}"),
        (DesktopInputOperation.KEY_CTRL_END, "^{END}"),
        (DesktopInputOperation.KEY_CTRL_HOME, "^{HOME}"),
        (DesktopInputOperation.KEY_CTRL_SHIFT_END, "^+{END}"),
        (DesktopInputOperation.KEY_CTRL_UP, "^{UP}"),
        (DesktopInputOperation.KEY_CTRL_V, "^v"),
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


def test_clipboard_text_requires_pinned_process_foreground_and_focus() -> None:
    app = _Window(101, "Any App", 11, (0, 0, 100, 100))
    other = _Window(202, "Other App", 22, (0, 0, 100, 100))
    adapter = _Adapter(app, other)
    adapter.clipboard_value = "中文 / XRF assay"

    result = adapter.clipboard_text(
        handle=101,
        expected_pid=11,
        expected_title="Any App",
    )

    assert result.text == adapter.clipboard_value
    assert result.length == len(adapter.clipboard_value)
    assert result.utf8_bytes == len(adapter.clipboard_value.encode("utf-8"))
    assert len(result.sha256) == 64

    adapter.focused_handle = other.handle
    with pytest.raises(RuntimeError, match="foreground and keyboard focus"):
        adapter.clipboard_text(
            handle=101,
            expected_pid=11,
            expected_title="Any App",
        )


def test_clipboard_text_rejects_response_larger_than_eight_mibibytes() -> None:
    window = _Window(101, "Any App", 11, (0, 0, 100, 100))
    adapter = _Adapter(window)
    adapter.clipboard_value = "x" * (8 * 1024 * 1024 + 1)

    with pytest.raises(RuntimeError, match="response limit"):
        adapter.clipboard_text(
            handle=101,
            expected_pid=11,
            expected_title="Any App",
        )


def test_clipboard_snapshot_is_pinned_and_returns_bounded_typed_formats() -> None:
    window = _Window(101, "Any App", 11, (0, 0, 100, 100))
    adapter = _Adapter(window)
    expected = DesktopClipboardSnapshot(
        formats=(DesktopClipboardFormat(13, "CF_UNICODETEXT", "text", text="中文"),),
        format_count=1,
        returned_data_bytes=6,
        truncated=False,
    )
    adapter._clipboard_snapshot = lambda: expected  # type: ignore[method-assign]

    result = adapter.clipboard_snapshot(
        handle=101,
        expected_pid=11,
        expected_title="Any App",
    )

    assert result == expected


def test_clipboard_format_entry_encodes_bytes_and_enforces_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, returned = UniversalDesktopAdapter._clipboard_format_entry(
        format_id=49152,
        name="Vendor Format",
        data=b"\x00\xff",
        remaining_bytes=100,
    )
    unsupported, unsupported_returned = UniversalDesktopAdapter._clipboard_format_entry(
        format_id=2,
        name="CF_BITMAP",
        data=12345,
        remaining_bytes=100,
    )
    total_limited, total_returned = UniversalDesktopAdapter._clipboard_format_entry(
        format_id=49152,
        name="Vendor Format",
        data=b"\x00\xff",
        remaining_bytes=3,
    )
    monkeypatch.setattr(adapter_module, "_MAX_CLIPBOARD_FORMAT_BYTES", 1)
    individual_limited, individual_returned = UniversalDesktopAdapter._clipboard_format_entry(
        format_id=49152,
        name="Vendor Format",
        data=b"\x00\xff",
        remaining_bytes=100,
    )

    assert binary.data_base64 == "AP8="
    assert binary.byte_length == 2
    assert len(binary.sha256) == 64
    assert returned == 4
    assert unsupported.data_type == "int"
    assert unsupported.error == "unsupported clipboard data type"
    assert unsupported_returned == 0
    assert total_limited.data_base64 is None
    assert "total data limit" in total_limited.error
    assert total_returned == 0
    assert individual_limited.data_base64 is None
    assert "individual data limit" in individual_limited.error
    assert individual_returned == 0


def test_registered_clipboard_handle_falls_back_to_bounded_hglobal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Clipboard:
        def OpenClipboard(self) -> None:
            pass

        def CloseClipboard(self) -> None:
            pass

        def EnumClipboardFormats(self, current: int) -> int:
            return 49152 if current == 0 else 0

        def GetClipboardFormatName(self, format_id: int) -> str:
            assert format_id == 49152
            return "KV Custom Format"

        def GetClipboardData(self, format_id: int) -> int:
            assert format_id == 49152
            return 12345

    monkeypatch.setitem(sys.modules, "win32clipboard", _Clipboard())
    monkeypatch.setattr(
        UniversalDesktopAdapter,
        "_clipboard_hglobal_bytes",
        staticmethod(lambda format_id: (b"\x01\x02", 2, "")),
    )

    snapshot = UniversalDesktopAdapter._clipboard_snapshot()

    assert snapshot.format_count == 1
    assert snapshot.formats[0].name == "KV Custom Format"
    assert snapshot.formats[0].data_base64 == "AQI="
    assert snapshot.formats[0].byte_length == 2


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


def test_dpi_awareness_is_initialized_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class _User32:
        @staticmethod
        def SetProcessDpiAwarenessContext(context: object) -> bool:
            del context
            calls.append(1)
            return True

    class _Windll:
        user32 = _User32()

    monkeypatch.setattr(adapter_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(adapter_module.ctypes, "windll", _Windll(), raising=False)
    monkeypatch.setattr(adapter_module, "_DPI_INITIALIZED", False)

    adapter_module.initialize_windows_dpi_awareness()
    adapter_module.initialize_windows_dpi_awareness()

    assert calls == [1]
