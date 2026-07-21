"""Application-agnostic, explicitly pinned Windows eyes/hands primitives."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import platform
import threading
import time
from contextlib import suppress
from io import BytesIO
from typing import Any

from .models import DesktopFrame, DesktopInputOperation, DesktopWindow

_MAX_FRAME_PIXELS = 50_000_000
_MAX_PNG_BYTES = 64 * 1024 * 1024
_MAX_TEXT_LENGTH = 4096
_MAX_DIALOG_WINDOWS = 64
_CLIPBOARD_LOCK = threading.Lock()
_DPI_LOCK = threading.Lock()
_DPI_INITIALIZED = False


def initialize_windows_dpi_awareness() -> None:
    """Use physical screen coordinates before capturing or injecting input.

    The calls are deliberately best-effort: Windows refuses to change DPI mode
    after another library has created a window, which is not a worker failure.
    """
    global _DPI_INITIALIZED
    if platform.system() != "Windows" or _DPI_INITIALIZED:
        return
    with _DPI_LOCK:
        if _DPI_INITIALIZED:
            return
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                _DPI_INITIALIZED = True
                return
        except (AttributeError, OSError):
            pass
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) in (0, -2147024891):
                _DPI_INITIALIZED = True
                return
        except (AttributeError, OSError):
            pass
        with suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
        _DPI_INITIALIZED = True


class UniversalDesktopAdapter:
    """Operate only on an explicitly selected top-level HWND or owned dialog.

    This surface cannot launch processes, execute shell commands, or communicate
    with PLCs. Every snapshot and input revalidates HWND, PID, and exact title.
    """

    def __init__(self) -> None:
        initialize_windows_dpi_awareness()

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
        # Native owned windows can be omitted by pywinauto's top-level list.
        # GW_OWNER distinguishes application-owned popups/dialogs from arbitrary
        # child controls without relying on toolkit- or application-specific classes.
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

        image = self._capture_window(handle, bounds)
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
        _preserve_child_focus: bool = False,
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
        allowed_foreground = self._allowed_foreground_handles(
            window, handle=handle, expected_pid=expected_pid
        )
        # Re-focusing a modal before every key can reset its active child
        # control. Preserve child focus when the pinned HWND or same-process
        # owner is already foreground.
        if (
            not _preserve_child_focus
            and self._foreground_window_handle() not in allowed_foreground
        ):
            window.set_focus()
        current = self._select_window(handle, expected_pid, expected_title)
        if self._bounds(current) != bounds:
            raise RuntimeError("selected window bounds changed before input")
        foreground = self._foreground_window_handle()
        if foreground not in allowed_foreground:
            raise RuntimeError("selected window did not receive foreground focus")
        if _preserve_child_focus and selected_operation not in pointer_operations:
            focused = self._focused_window_handle()
            if not focused or self._window_process_id(focused) != expected_pid:
                raise RuntimeError("keyboard focus left the selected application")

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
            self._paste_unicode(
                window, text, preserve_child_focus=_preserve_child_focus
            )
        else:
            key = {
                DesktopInputOperation.KEY_ENTER: "{ENTER}",
                DesktopInputOperation.KEY_ESCAPE: "{ESC}",
                DesktopInputOperation.KEY_CTRL_A: "^a",
                DesktopInputOperation.KEY_F2: "{F2}",
                DesktopInputOperation.TAB: "{TAB}",
                DesktopInputOperation.SHIFT_TAB: "+{TAB}",
            }[selected_operation]
            if _preserve_child_focus:
                self._send_keys(key)
            else:
                window.type_keys(key, set_foreground=False)
        self._select_window(handle, expected_pid, expected_title)
        return True

    def input_sequence(
        self,
        *,
        handle: int,
        expected_pid: int,
        expected_title: str,
        operations: tuple[dict[str, Any], ...],
    ) -> int:
        """Run one pinned sequence without redirecting keys from a clicked child."""
        completed = 0
        for index, step in enumerate(operations):
            if not self.input(
                handle=handle,
                expected_pid=expected_pid,
                expected_title=expected_title,
                operation=str(step["operation"]),
                x=step.get("x"),
                y=step.get("y"),
                delta=step.get("delta"),
                text=str(step.get("text", "")),
                _preserve_child_focus=index > 0,
            ):
                break
            completed += 1
            pause_ms = int(step.get("pause_ms", 0) or 0)
            if index + 1 < len(operations) and pause_ms:
                time.sleep(pause_ms / 1000)
        return completed

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

    def _is_owned_dialog(self, window, *, owner=None) -> bool:  # type: ignore[no-untyped-def]
        """Return true for a bounded visible native window owned by the same app."""
        if not window.is_visible():
            return False
        if not self._has_sane_bounds(window):
            return False
        handle = int(getattr(window, "handle", 0) or 0)
        owner_handle = self._native_owner_handle(handle)
        if not owner_handle or owner_handle == handle:
            return False
        return self._window_process_id(owner_handle) == int(window.process_id())

    @classmethod
    def _has_sane_bounds(cls, window) -> bool:  # type: ignore[no-untyped-def]
        try:
            left, top, right, bottom = cls._bounds(window)
        except Exception:
            return False
        width = right - left
        height = bottom - top
        return width > 0 and height > 0 and width * height <= _MAX_FRAME_PIXELS

    def _window_snapshot(self, window) -> DesktopWindow:  # type: ignore[no-untyped-def]
        handle = int(window.handle)
        return DesktopWindow(
            handle=handle,
            title=window.window_text(),
            process_id=int(window.process_id()),
            bounds=UniversalDesktopAdapter._bounds(window),
            minimized=bool(window.is_minimized()),
            owner_handle=self._native_owner_handle(handle),
            foreground=self._foreground_window_handle() == handle,
            enabled=bool(window.is_enabled()),
            class_name=str(window.class_name()),
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

    def _capture_window(
        self, handle: int, bounds: tuple[int, int, int, int]
    ):  # type: ignore[no-untyped-def]
        """Capture an HWND when occluded, with a physical-screen fallback."""
        expected_size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
        try:
            image = self._print_window(handle, expected_size)
            if (
                (int(image.width), int(image.height)) == expected_size
                and not self._image_is_blank(image)
            ):
                return image
        except Exception:
            pass
        return self._grab_bbox(bounds)

    @staticmethod
    def _print_window(handle: int, size: tuple[int, int]):  # type: ignore[no-untyped-def]
        """Render a native window into an in-memory bitmap using PrintWindow."""
        if platform.system() != "Windows":
            raise RuntimeError("PrintWindow is available only on Windows")
        import win32gui
        import win32ui
        from PIL import Image

        width, height = size
        window_dc = win32gui.GetWindowDC(handle)
        if not window_dc:
            raise RuntimeError("GetWindowDC failed")
        source_dc = None
        target_dc = None
        bitmap = None
        try:
            source_dc = win32ui.CreateDCFromHandle(window_dc)
            target_dc = source_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(source_dc, width, height)
            target_dc.SelectObject(bitmap)
            if not win32gui.PrintWindow(handle, target_dc.GetSafeHdc(), 2):
                raise RuntimeError("PrintWindow failed")
            bits = bitmap.GetBitmapBits(True)
            return Image.frombuffer(
                "RGB", (width, height), bits, "raw", "BGRX", 0, 1
            ).copy()
        finally:
            if bitmap is not None:
                with suppress(Exception):
                    win32gui.DeleteObject(bitmap.GetHandle())
            if target_dc is not None:
                with suppress(Exception):
                    target_dc.DeleteDC()
            if source_dc is not None:
                with suppress(Exception):
                    source_dc.DeleteDC()
            win32gui.ReleaseDC(handle, window_dc)

    @staticmethod
    def _image_is_blank(image) -> bool:  # type: ignore[no-untyped-def]
        extrema = image.convert("RGB").getextrema()
        return all(low == high for low, high in extrema)

    def _paste_unicode(
        self, window, text: str, *, preserve_child_focus: bool = False
    ) -> None:  # type: ignore[no-untyped-def]
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
                if preserve_child_focus:
                    self._send_keys("^v")
                else:
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
    def _send_keys(keys: str) -> None:
        from pywinauto.keyboard import send_keys

        send_keys(keys)

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
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)

    @staticmethod
    def _focused_window_handle() -> int:
        from ctypes import wintypes

        class GuiThreadInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if not ctypes.windll.user32.GetGUIThreadInfo(0, ctypes.byref(info)):
            return 0
        return int(info.hwndFocus or info.hwndActive or 0)

    @staticmethod
    def _native_owner_handle(handle: int) -> int:
        return int(ctypes.windll.user32.GetWindow(handle, 4) or 0)  # GW_OWNER

    def _allowed_foreground_handles(
        self, window, *, handle: int, expected_pid: int
    ) -> set[int]:  # type: ignore[no-untyped-def]
        allowed = {handle}
        wrapper_owner = window.top_level_parent()
        wrapper_owner_handle = int(getattr(wrapper_owner, "handle", 0) or 0)
        if wrapper_owner_handle and int(wrapper_owner.process_id()) == expected_pid:
            allowed.add(wrapper_owner_handle)
        native_owner_handle = self._native_owner_handle(handle)
        if (
            native_owner_handle
            and self._window_process_id(native_owner_handle) == expected_pid
        ):
            allowed.add(native_owner_handle)
        return allowed

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
