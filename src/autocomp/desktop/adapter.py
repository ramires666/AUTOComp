"""Application-agnostic, explicitly pinned Windows eyes/hands primitives."""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from io import BytesIO

from .models import DesktopFrame, DesktopInputOperation, DesktopWindow

_MAX_FRAME_PIXELS = 50_000_000
_MAX_PNG_BYTES = 64 * 1024 * 1024
_MAX_TEXT_LENGTH = 4096
_MAX_DIALOG_WINDOWS = 64
_DIALOG_CLASSES = frozenset({"#32770", "Window"})
_CLIPBOARD_LOCK = threading.Lock()


class UniversalDesktopAdapter:
    """Operate only on an explicitly selected top-level HWND or owned dialog.

    This surface cannot launch processes, execute shell commands, or communicate
    with PLCs. Every snapshot and input revalidates HWND, PID, and exact title.
    """

    def enumerate_windows(self) -> tuple[DesktopWindow, ...]:
        windows: list[DesktopWindow] = []
        seen_handles: set[int] = set()
        for candidate in self._desktop().windows(top_level_only=True, visible_only=True):
            try:
                if not candidate.is_visible():
                    continue
                snapshot = self._window_snapshot(candidate)
                if not self._has_sane_bounds(candidate):
                    continue
                windows.append(snapshot)
                seen_handles.add(snapshot.handle)
            except Exception:
                continue
        # Native modal dialogs can be omitted by pywinauto's top-level list.
        # Include only bounded, visible dialog windows -- never arbitrary child
        # controls -- so this remains an application-agnostic eyes/hands surface.
        dialog_count = 0
        for candidate in self._desktop().windows(top_level_only=False, visible_only=True):
            if dialog_count >= _MAX_DIALOG_WINDOWS:
                break
            try:
                handle = int(getattr(candidate, "handle", 0) or 0)
                if handle in seen_handles or not self._is_owned_dialog(candidate):
                    continue
                snapshot = self._window_snapshot(candidate)
                windows.append(snapshot)
                seen_handles.add(handle)
                dialog_count += 1
            except Exception:
                continue
        return tuple(windows)

    def snapshot(
        self,
        *,
        handle: int,
        expected_pid: int,
        expected_title: str,
    ) -> DesktopFrame:
        window = self._select_window(handle, expected_pid, expected_title)
        if window.is_minimized():
            raise RuntimeError("selected window is minimized")
        bounds = self._bounds(window)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        if width <= 0 or height <= 0 or width * height > _MAX_FRAME_PIXELS:
            raise RuntimeError("selected window frame has unsafe dimensions")

        image = self._grab_bbox(bounds)
        if (int(image.width), int(image.height)) != (width, height):
            raise RuntimeError("captured frame dimensions do not match selected window")
        stream = BytesIO()
        image.save(stream, format="PNG")
        png = stream.getvalue()
        if len(png) > _MAX_PNG_BYTES:
            raise RuntimeError("captured PNG exceeds the response limit")
        current = self._select_window(handle, expected_pid, expected_title)
        if self._bounds(current) != bounds:
            raise RuntimeError("selected window bounds changed during capture")
        return DesktopFrame(
            handle=handle,
            title=expected_title,
            process_id=expected_pid,
            bounds=bounds,
            width=width,
            height=height,
            png_base64=base64.b64encode(png).decode("ascii"),
            png_sha256=hashlib.sha256(png).hexdigest(),
        )

    def input(
        self,
        *,
        handle: int,
        expected_pid: int,
        expected_title: str,
        operation: str | DesktopInputOperation,
        x: int | None = None,
        y: int | None = None,
        delta: int | None = None,
        text: str = "",
    ) -> bool:
        try:
            selected_operation = DesktopInputOperation(operation)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported desktop input operation") from exc
        window = self._select_window(handle, expected_pid, expected_title)
        if window.is_minimized() or not window.is_enabled():
            raise RuntimeError("selected window is not ready for input")
        bounds = self._bounds(window)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]

        pointer_operations = {
            DesktopInputOperation.CLICK,
            DesktopInputOperation.RIGHT,
            DesktopInputOperation.DOUBLE,
            DesktopInputOperation.WHEEL,
        }
        coordinates: tuple[int, int] | None = None
        if selected_operation in pointer_operations:
            if (
                not isinstance(x, int)
                or isinstance(x, bool)
                or not isinstance(y, int)
                or isinstance(y, bool)
            ):
                raise ValueError("pointer input requires integer x and y")
            if not 0 <= x < width or not 0 <= y < height:
                raise ValueError("input coordinates are outside the selected window frame")
            coordinates = (x, y)
        elif x is not None or y is not None:
            raise ValueError("x and y are valid only for pointer operations")
        if selected_operation is not DesktopInputOperation.WHEEL and delta is not None:
            raise ValueError("delta is valid only for wheel input")
        if selected_operation is not DesktopInputOperation.TYPE_TEXT and text:
            raise ValueError("text is valid only for type_text input")
        window.set_focus()
        current = self._select_window(handle, expected_pid, expected_title)
        if self._bounds(current) != bounds:
            raise RuntimeError("selected window bounds changed before input")
        foreground = self._foreground_window_handle()
        allowed_foreground = {handle}
        wrapper_owner = window.top_level_parent()
        wrapper_owner_handle = int(getattr(wrapper_owner, "handle", 0) or 0)
        if (
            wrapper_owner_handle
            and int(wrapper_owner.process_id()) == expected_pid
        ):
            allowed_foreground.add(wrapper_owner_handle)
        # Some native modal dialogs are themselves reported as top-level by
        # pywinauto, while SetFocus/GetForegroundWindow resolves to their main
        # owner.  Read the actual Win32 owner so pinned dialog input still works.
        native_owner_handle = self._native_owner_handle(handle)
        if (
            native_owner_handle
            and self._window_process_id(native_owner_handle) == expected_pid
        ):
            allowed_foreground.add(native_owner_handle)
        if foreground not in allowed_foreground:
            raise RuntimeError("selected window did not receive foreground focus")

        if selected_operation is DesktopInputOperation.CLICK:
            window.click_input(button="left", coords=coordinates, absolute=False)
        elif selected_operation is DesktopInputOperation.RIGHT:
            window.click_input(button="right", coords=coordinates, absolute=False)
        elif selected_operation is DesktopInputOperation.DOUBLE:
            window.double_click_input(button="left", coords=coordinates)
        elif selected_operation is DesktopInputOperation.WHEEL:
            if (
                not isinstance(delta, int)
                or isinstance(delta, bool)
                or not -12 <= delta <= 12
                or delta == 0
            ):
                raise ValueError("wheel delta must be a non-zero integer from -12 to 12")
            window.wheel_mouse_input(wheel_dist=delta, coords=coordinates)
        elif selected_operation is DesktopInputOperation.TYPE_TEXT:
            self._paste_unicode(window, text)
        else:
            key = {
                DesktopInputOperation.KEY_ENTER: "{ENTER}",
                DesktopInputOperation.KEY_ESCAPE: "{ESC}",
                DesktopInputOperation.KEY_CTRL_A: "^a",
                DesktopInputOperation.KEY_F2: "{F2}",
                DesktopInputOperation.TAB: "{TAB}",
                DesktopInputOperation.SHIFT_TAB: "+{TAB}",
            }[selected_operation]
            window.type_keys(key, set_foreground=False)
        self._select_window(handle, expected_pid, expected_title)
        return True

    def _select_window(self, handle: int, expected_pid: int, expected_title: str):  # type: ignore[no-untyped-def]
        if handle <= 0 or expected_pid <= 0:
            raise ValueError("exact handle, PID, and title precondition is required")
        try:
            window = self._desktop().window(handle=handle).wrapper_object()
        except Exception as exc:
            raise RuntimeError("selected window no longer exists") from exc
        actual_handle = int(getattr(window, "handle", 0) or 0)
        if actual_handle != handle:
            raise RuntimeError("selected window handle changed")
        owner = window.top_level_parent()
        owner_handle = int(getattr(owner, "handle", 0) or 0)
        is_top_level = owner_handle == handle
        if not is_top_level and not self._is_owned_dialog(window, owner=owner):
            raise RuntimeError("selected handle is not a top-level window or owned dialog")
        if int(window.process_id()) != expected_pid or window.window_text() != expected_title:
            raise RuntimeError("selected window identity precondition failed")
        if not window.is_visible():
            raise RuntimeError("selected window is not visible")
        return window

    @classmethod
    def _is_owned_dialog(cls, window, *, owner=None) -> bool:  # type: ignore[no-untyped-def]
        """Return true only for a visible, bounded native dialog owned by its app."""
        if not window.is_visible() or str(window.class_name()) not in _DIALOG_CLASSES:
            return False
        if not cls._has_sane_bounds(window):
            return False
        owner = owner if owner is not None else window.top_level_parent()
        owner_handle = int(getattr(owner, "handle", 0) or 0)
        return (
            owner_handle != int(getattr(window, "handle", 0) or 0)
            and int(owner.process_id()) == int(window.process_id())
            and bool(owner.is_visible())
        )

    @classmethod
    def _has_sane_bounds(cls, window) -> bool:  # type: ignore[no-untyped-def]
        try:
            left, top, right, bottom = cls._bounds(window)
        except Exception:
            return False
        width = right - left
        height = bottom - top
        return width > 0 and height > 0 and width * height <= _MAX_FRAME_PIXELS

    @staticmethod
    def _window_snapshot(window) -> DesktopWindow:  # type: ignore[no-untyped-def]
        return DesktopWindow(
            handle=int(window.handle),
            title=window.window_text(),
            process_id=int(window.process_id()),
            bounds=UniversalDesktopAdapter._bounds(window),
            minimized=bool(window.is_minimized()),
        )

    @staticmethod
    def _bounds(window) -> tuple[int, int, int, int]:  # type: ignore[no-untyped-def]
        rectangle = window.rectangle()
        return (
            int(rectangle.left),
            int(rectangle.top),
            int(rectangle.right),
            int(rectangle.bottom),
        )

    @staticmethod
    def _grab_bbox(bounds: tuple[int, int, int, int]):  # type: ignore[no-untyped-def]
        from PIL import ImageGrab

        return ImageGrab.grab(bbox=bounds, all_screens=True)

    @staticmethod
    def _paste_unicode(window, text: str) -> None:  # type: ignore[no-untyped-def]
        if (
            not text
            or len(text) > _MAX_TEXT_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in text)
        ):
            raise ValueError("text must be bounded, non-empty, and printable")
        import win32clipboard

        with _CLIPBOARD_LOCK:
            previous: str | None = None
            had_unicode = False
            UniversalDesktopAdapter._open_clipboard(win32clipboard)
            try:
                had_unicode = bool(
                    win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT)
                )
                if had_unicode:
                    previous = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            try:
                window.type_keys("^v", set_foreground=False)
                time.sleep(0.05)
            finally:
                UniversalDesktopAdapter._open_clipboard(win32clipboard)
                try:
                    # Only CF_UNICODETEXT can be safely reconstructed by this
                    # primitive; callers should not rely on other clipboard formats.
                    win32clipboard.EmptyClipboard()
                    if had_unicode and previous is not None:
                        win32clipboard.SetClipboardText(previous, win32clipboard.CF_UNICODETEXT)
                finally:
                    win32clipboard.CloseClipboard()

    @staticmethod
    def _open_clipboard(win32clipboard) -> None:  # type: ignore[no-untyped-def]
        for attempt in range(5):
            try:
                win32clipboard.OpenClipboard()
                return
            except Exception:
                if attempt == 4:
                    raise RuntimeError("Windows clipboard remained busy") from None
                time.sleep(0.02)

    @staticmethod
    def _foreground_window_handle() -> int:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)

    @staticmethod
    def _native_owner_handle(handle: int) -> int:
        import ctypes

        return int(ctypes.windll.user32.GetWindow(handle, 4) or 0)  # GW_OWNER

    @staticmethod
    def _window_process_id(handle: int) -> int:
        import ctypes

        process_id = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        return int(process_id.value)

    @staticmethod
    def _desktop():  # type: ignore[no-untyped-def]
        if __import__("platform").system() != "Windows":
            raise RuntimeError("universal desktop automation is available only on Windows")
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise RuntimeError("install the windows extra to use desktop automation") from exc
        return Desktop(backend="win32")
