"""Adapters isolate optional Windows UI Automation dependencies."""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Protocol

from .models import (
    ControlSnapshot,
    MenuItemSnapshot,
    ProjectTreeInventory,
    ProjectTreeNodeSnapshot,
    TreeItemMenuInspection,
    TreeItemRenameResult,
    VisualInputOperation,
    VisualSnapshot,
    WindowSnapshot,
    WindowState,
)

_EXPANSION_STATES = {
    0: "collapsed",
    1: "expanded",
    2: "partially_expanded",
    3: "leaf",
}

_TVM_GETEDITCONTROL = 0x110F
_TREE_RENAME_KEYS = frozenset({"{F2}", "{ENTER}", "{ESC}"})


class KVStudioAdapter(Protocol):
    """Minimal adapter surface; implementations must never access a PLC."""

    def discover(self) -> tuple[WindowSnapshot, ...]:
        """Return only KV STUDIO 11.62 windows."""

    def status(self) -> WindowState:
        """Return state for the single allowlisted local editor without changing it."""

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        """Expand a tree item in the already allowlisted local editor window."""

    def inventory_project_tree(
        self, *, expand_all: bool, restore_state: bool
    ) -> ProjectTreeInventory:
        """Inventory the native project tree with optional reversible expansion."""

    def activate_tree_item(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
    ) -> bool:
        """Open exactly one pinned project-tree item without editing its text."""

    def rename_tree_item(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
        target: str,
    ) -> TreeItemRenameResult:
        """Rename exactly one pinned tree item and roll back on failed verification."""

    def inspect_tree_item_menu(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
    ) -> TreeItemMenuInspection:
        """Open, snapshot, and close the context menu for one pinned tree item."""

    def visual_snapshot(self) -> VisualSnapshot:
        """Capture the unique KV editor client area."""

    def visual_input(
        self,
        operation: VisualInputOperation,
        *,
        x: int | None,
        y: int | None,
        delta: int | None,
        text: str,
    ) -> bool:
        """Send one constrained visual input to the unique KV editor."""


class FakeKVStudioAdapter:
    """Deterministic adapter for tests; it has no OS or network side effects."""

    def __init__(self, windows: tuple[WindowSnapshot, ...] = ()) -> None:
        self.windows = windows
        self.expanded_paths: list[tuple[str, ...]] = []
        self.project_tree_inventory = ProjectTreeInventory(
            "KV STUDIO", 0, "ProjectTreeView", 0, 0, 0, True
        )
        self.window_state = WindowState("KV STUDIO", 0, False, True, True, True)
        self.tree_items: dict[tuple[int, ...], tuple[str, ...]] = {}
        self.activation_calls: list[dict[str, object]] = []
        self.rename_calls: list[dict[str, object]] = []
        self.menu_inspection_calls: list[dict[str, object]] = []
        self.visual_input_calls: list[dict[str, object]] = []
        self.rename_failure_after_write = False

    def discover(self) -> tuple[WindowSnapshot, ...]:
        return self.windows

    def status(self) -> WindowState:
        return self.window_state

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        self.expanded_paths.append(target_path)
        return bool(target_path)

    def inventory_project_tree(
        self, *, expand_all: bool, restore_state: bool
    ) -> ProjectTreeInventory:
        del expand_all, restore_state
        return self.project_tree_inventory

    def activate_tree_item(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
    ) -> bool:
        self.activation_calls.append(
            {
                "locator": locator,
                "expected_path": expected_path,
                "expected_source": expected_source,
            }
        )
        actual_path = self.tree_items.get(locator)
        return bool(
            actual_path == expected_path
            and actual_path
            and actual_path[-1] == expected_source
        )

    def rename_tree_item(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
        target: str,
    ) -> TreeItemRenameResult:
        self.rename_calls.append(
            {
                "locator": locator,
                "expected_path": expected_path,
                "expected_source": expected_source,
                "target": target,
            }
        )
        actual_path = self.tree_items.get(locator)
        if actual_path != expected_path or not actual_path or actual_path[-1] != expected_source:
            actual = actual_path[-1] if actual_path else ""
            return TreeItemRenameResult(False, actual, actual, error="source precondition failed")
        new_path = (*actual_path[:-1], target)
        self.tree_items[locator] = new_path
        if self.rename_failure_after_write:
            self.tree_items[locator] = actual_path
            return TreeItemRenameResult(
                False,
                expected_source,
                expected_source,
                rollback_attempted=True,
                rollback_succeeded=True,
                error="post-rename verification failed",
            )
        return TreeItemRenameResult(True, expected_source, target)

    def inspect_tree_item_menu(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
    ) -> TreeItemMenuInspection:
        self.menu_inspection_calls.append(
            {
                "locator": locator,
                "expected_path": expected_path,
                "expected_source": expected_source,
            }
        )
        actual_path = self.tree_items.get(locator)
        complete = actual_path == expected_path and expected_path[-1] == expected_source
        return TreeItemMenuInspection(
            "KV STUDIO",
            0,
            locator,
            expected_path,
            expected_source,
            items=(MenuItemSnapshot("重命名", automation_id="rename", enabled=True),)
            if complete
            else (),
            complete=complete,
            warnings=() if complete else ("source precondition failed",),
        )

    def visual_snapshot(self) -> VisualSnapshot:
        return VisualSnapshot(
            "iVBORw0KGgo=", (0, 0, 100, 80), (1, 2, 99, 78), 98, 76, 0, "KV STUDIO"
        )

    def visual_input(
        self,
        operation: VisualInputOperation,
        *,
        x: int | None,
        y: int | None,
        delta: int | None,
        text: str,
    ) -> bool:
        self.visual_input_calls.append(
            {"operation": operation, "x": x, "y": y, "delta": delta, "text": text}
        )
        return True


class PywinautoKVStudioAdapter:
    """Allowlisted offline UI adapter for a locally running KV STUDIO 11.62.

    ``pywinauto`` is imported only inside methods so non-Windows inventory and
    translation workflows can import this package safely. The only text mutation
    primitive is an exact, verified ProjectTreeView rename with automatic rollback;
    this adapter exposes no PLC, shell, arbitrary-window, or arbitrary-key APIs.
    """

    def __init__(
        self,
        title_pattern: str = r"\bKV STUDIO\b",
        *,
        max_depth: int = 12,
        max_controls: int = 5000,
        max_project_depth: int = 64,
        max_project_items: int = 50_000,
        max_project_expansions: int = 2_000,
        max_project_seconds: float = 120.0,
        expansion_settle_seconds: float = 0.05,
    ) -> None:
        if (
            max_depth < 1
            or max_controls < 1
            or max_project_depth < 1
            or max_project_items < 1
            or max_project_expansions < 1
            or max_project_seconds <= 0
            or not 0 <= expansion_settle_seconds <= 2
        ):
            raise ValueError("UI inventory limits must be positive")
        self._title_pattern = re.compile(title_pattern, re.IGNORECASE)
        self._max_depth = max_depth
        self._max_controls = max_controls
        self._max_project_depth = max_project_depth
        self._max_project_items = max_project_items
        self._max_project_expansions = max_project_expansions
        self._max_project_seconds = max_project_seconds
        self._expansion_settle_seconds = expansion_settle_seconds

    def discover(self) -> tuple[WindowSnapshot, ...]:
        desktop = self._desktop()
        snapshots: list[WindowSnapshot] = []
        for window in desktop.windows():
            title = window.window_text()
            if not self._is_allowed_title(title):
                continue
            budget = [self._max_controls]
            snapshots.append(
                WindowSnapshot(
                    title=title,
                    process_id=int(window.process_id()),
                    controls=self._snapshot_children(window, depth=0, budget=budget),
                )
            )
        return tuple(snapshots)

    def status(self) -> WindowState:
        """Inspect, but never restore or focus, the primary allowlisted editor."""
        windows = self._allowed_windows()
        if not windows:
            raise RuntimeError("KV STUDIO editor window was not found")
        with_project_tree = tuple(
            (window, trees) for window in windows if (trees := self._project_trees(window))
        )
        if len(with_project_tree) > 1:
            raise RuntimeError(
                "multiple KV STUDIO project editors were found; keep one project open"
            )
        if with_project_tree:
            window, trees = with_project_tree[0]
        elif len(windows) == 1:
            window, trees = windows[0], ()
        else:
            raise RuntimeError(
                "multiple KV STUDIO windows were found but no unique project editor exists"
            )
        return WindowState(
            title=window.window_text(),
            process_id=int(window.process_id()),
            minimized=self._is_minimized(window),
            enabled=self._safe_control_flag(window, "is_enabled"),
            visible=self._safe_control_flag(window, "is_visible"),
            project_tree_available=bool(trees),
        )

    def visual_snapshot(self) -> VisualSnapshot:
        """Capture only the unique, visible KV editor's native client area."""
        import base64
        from io import BytesIO

        from PIL import ImageGrab

        window, _ = self._find_project_tree()
        if self._is_minimized(window) or not window.is_visible():
            raise RuntimeError("KV STUDIO must be visible and not minimized for a snapshot")
        window_bounds, client_bounds = self._native_window_bounds(window)
        # Grab the exact on-screen client rectangle. This is more reliable for
        # the WinForms/Win32 mixture used by Chinese KV STUDIO than UIA's
        # capture_as_image implementation.
        client_image = ImageGrab.grab(
            bbox=client_bounds,
            include_layered_windows=True,
            all_screens=True,
        )
        stream = BytesIO()
        client_image.save(stream, format="PNG")
        return VisualSnapshot(
            png_base64=base64.b64encode(stream.getvalue()).decode("ascii"),
            window_bounds=window_bounds,
            client_bounds=client_bounds,
            width=int(client_image.width),
            height=int(client_image.height),
            process_id=int(window.process_id()),
            window_title=window.window_text(),
        )

    def visual_input(
        self,
        operation: VisualInputOperation,
        *,
        x: int | None,
        y: int | None,
        delta: int | None,
        text: str,
    ) -> bool:
        """Send one bounded input to the unique allowlisted KV editor window."""
        window, _ = self._find_project_tree()
        self._restore_and_focus_editor(window)
        window_bounds, client_bounds = self._native_window_bounds(window)
        coordinate_operations = {
            VisualInputOperation.CLICK,
            VisualInputOperation.RIGHT_CLICK,
            VisualInputOperation.DOUBLE_CLICK,
            VisualInputOperation.WHEEL,
        }
        relative: tuple[int, int] | None = None
        if operation in coordinate_operations:
            if x is None or y is None:
                raise ValueError("visual coordinate operation requires x and y")
            width = client_bounds[2] - client_bounds[0]
            height = client_bounds[3] - client_bounds[1]
            if not 0 <= x < width or not 0 <= y < height:
                raise ValueError("visual coordinates are outside the KV STUDIO client area")
            relative = (
                client_bounds[0] + x - window_bounds[0],
                client_bounds[1] + y - window_bounds[1],
            )

        if operation is VisualInputOperation.CLICK:
            window.click_input(button="left", coords=relative)
        elif operation is VisualInputOperation.RIGHT_CLICK:
            window.click_input(button="right", coords=relative)
        elif operation is VisualInputOperation.DOUBLE_CLICK:
            window.double_click_input(button="left", coords=relative)
        elif operation is VisualInputOperation.WHEEL:
            if delta is None or delta == 0:
                raise ValueError("wheel requires a non-zero delta")
            window.wheel_mouse_input(wheel_dist=delta, coords=relative)
        elif operation is VisualInputOperation.TYPE_TEXT:
            self._paste_unicode_text(window, text)
        else:
            keys = {
                VisualInputOperation.KEY_ENTER: "{ENTER}",
                VisualInputOperation.KEY_ESCAPE: "{ESC}",
                VisualInputOperation.KEY_F2: "{F2}",
                VisualInputOperation.KEY_CTRL_A: "^a",
            }
            try:
                key = keys[operation]
            except KeyError as exc:
                raise ValueError("unsupported visual input operation") from exc
            window.type_keys(key, set_foreground=False)
        return True

    @staticmethod
    def _paste_unicode_text(window, text: str) -> None:  # type: ignore[no-untyped-def]
        if not text or any(ord(character) < 32 or ord(character) == 127 for character in text):
            raise ValueError("visual text must be non-empty printable text")
        import win32clipboard

        previous: str | None = None
        had_unicode = False
        win32clipboard.OpenClipboard()
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
        finally:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                if had_unicode and previous is not None:
                    win32clipboard.SetClipboardText(previous, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()

    @staticmethod
    def _native_window_bounds(
        window,
    ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:  # type: ignore[no-untyped-def]
        import ctypes
        from ctypes import wintypes

        handle = int(getattr(window.element_info, "handle", 0) or 0)
        if not handle:
            raise RuntimeError("KV STUDIO window has no native handle")
        window_rect = wintypes.RECT()
        client_rect = wintypes.RECT()
        top_left = wintypes.POINT(0, 0)
        bottom_right = wintypes.POINT()
        user32 = ctypes.windll.user32
        if not user32.GetWindowRect(handle, ctypes.byref(window_rect)):
            raise RuntimeError("could not read KV STUDIO window bounds")
        if not user32.GetClientRect(handle, ctypes.byref(client_rect)):
            raise RuntimeError("could not read KV STUDIO client bounds")
        bottom_right.x = client_rect.right
        bottom_right.y = client_rect.bottom
        if not user32.ClientToScreen(handle, ctypes.byref(top_left)) or not user32.ClientToScreen(
            handle, ctypes.byref(bottom_right)
        ):
            raise RuntimeError("could not map KV STUDIO client bounds to screen")
        window_bounds = (
            int(window_rect.left),
            int(window_rect.top),
            int(window_rect.right),
            int(window_rect.bottom),
        )
        client_bounds = (
            int(top_left.x),
            int(top_left.y),
            int(bottom_right.x),
            int(bottom_right.y),
        )
        if client_bounds[2] <= client_bounds[0] or client_bounds[3] <= client_bounds[1]:
            raise RuntimeError("KV STUDIO client area is empty")
        return window_bounds, client_bounds

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        """Scaffold only: tree expansion is intentionally not implemented yet."""
        del target_path
        return False

    def inventory_project_tree(
        self, *, expand_all: bool, restore_state: bool = True
    ) -> ProjectTreeInventory:
        """Capture every accessible project node, restoring expansion state by default."""
        if not restore_state:
            raise ValueError("project-tree inventory must restore its temporary expansion state")
        window, tree = self._find_project_tree()
        budget = [self._max_project_items]
        expansion_budget = [self._max_project_expansions]
        deadline = time.monotonic() + self._max_project_seconds
        expanded: list[tuple[tuple[int, ...], tuple[str, ...]]] = []
        warnings: list[str] = []
        counters = {"items": 0, "restored": 0}
        truncated = [False]
        root_nodes: list[ProjectTreeNodeSnapshot] = []
        try:
            try:
                root_controls = self._tree_item_children(tree)
            except Exception as exc:
                raise RuntimeError("could not enumerate KV STUDIO project-tree roots") from exc
            if not root_controls:
                truncated[0] = True
                warnings.append("project tree contains no accessible root items")
            for sibling_index, child in enumerate(root_controls):
                if budget[0] <= 0 or time.monotonic() >= deadline:
                    truncated[0] = True
                    warnings.append("project-tree inventory limit reached before all roots")
                    break
                root_nodes.append(
                    self._crawl_project_node(
                        child,
                        parent_path=(),
                        parent_locator=(),
                        sibling_index=sibling_index,
                        depth=0,
                        expand_all=expand_all,
                        budget=budget,
                        expansion_budget=expansion_budget,
                        deadline=deadline,
                        expanded=expanded,
                        warnings=warnings,
                        counters=counters,
                        truncated=truncated,
                    )
                )
        except Exception as exc:
            truncated[0] = True
            warnings.append(f"project-tree traversal failed: {type(exc).__name__}: {exc}")
        finally:
            if restore_state:
                restoration_tree = tree
                for locator, path in reversed(expanded):
                    try:
                        try:
                            control = self._resolve_tree_item(restoration_tree, locator, path)
                            self._collapse_and_verify(control)
                        except Exception:
                            _, restoration_tree = self._find_project_tree()
                            control = self._resolve_tree_item(restoration_tree, locator, path)
                            self._collapse_and_verify(control)
                        counters["restored"] += 1
                    except Exception:
                        warnings.append(f"could not restore collapsed state: {' > '.join(path)}")

        roots = tuple(root_nodes)
        info = tree.element_info
        restoration_complete = not restore_state or counters["restored"] == len(expanded)
        if int(window.process_id()) != int(getattr(info, "process_id", window.process_id())):
            warnings.append("project tree process identity changed during inventory")
            truncated[0] = True
        return ProjectTreeInventory(
            window_title=window.window_text(),
            process_id=int(window.process_id()),
            automation_id=str(info.automation_id or ""),
            item_count=counters["items"],
            expanded_count=len(expanded),
            restored_count=counters["restored"],
            restore_requested=restore_state,
            complete=not truncated[0] and restoration_complete,
            restoration_complete=restoration_complete,
            truncated=truncated[0],
            warnings=tuple(warnings),
            roots=roots,
        )

    def activate_tree_item(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
    ) -> bool:
        """Open one exact tree item after rechecking its full indexed identity."""
        window, tree = self._find_project_tree()
        window_identity = (window.window_text(), int(window.process_id()))
        self._restore_and_focus_editor(window)
        item = self._resolve_tree_item_for_edit(
            tree,
            locator,
            expected_path,
            expanded=[],
        )
        if item.window_text().strip() != expected_source:
            raise RuntimeError("source precondition failed")
        item.ensure_visible()
        current_window, current_tree = self._find_project_tree()
        if (current_window.window_text(), int(current_window.process_id())) != window_identity:
            raise RuntimeError("KV STUDIO window identity changed before tree activation")
        item = self._resolve_tree_item_for_edit(
            current_tree,
            locator,
            expected_path,
            expanded=[],
        )
        if item.window_text().strip() != expected_source:
            raise RuntimeError("tree-item identity changed before activation")
        item.select()
        if item.window_text().strip() != expected_source:
            raise RuntimeError("tree-item identity changed after selection")
        item.double_click_input(button="left")
        return True

    def rename_tree_item(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
        target: str,
    ) -> TreeItemRenameResult:
        """Perform one exact, verified edit inside ``ProjectTreeView`` only."""
        before = ""
        after = ""
        rollback_attempted = False
        rollback_succeeded = False
        expanded: list[object] = []
        result: TreeItemRenameResult
        try:
            window, tree = self._find_project_tree()
            window_identity = (window.window_text(), int(window.process_id()))
            self._restore_and_focus_editor(window)
            item = self._resolve_tree_item_for_edit(
                tree,
                locator,
                expected_path,
                expanded=expanded,
            )
            before = item.window_text().strip()
            if before != expected_source or expected_path[-1] != expected_source:
                result = TreeItemRenameResult(
                    False,
                    before,
                    before,
                    error="source precondition failed",
                )
            else:
                self._commit_tree_item_text(window, tree, item, target)
                current = self._wait_for_tree_text(
                    tree,
                    locator,
                    target,
                    expanded=expanded,
                    timeout_seconds=2.0,
                )
                after = current.window_text().strip()
                result = TreeItemRenameResult(True, before, after)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            try:
                if "tree" in locals():
                    try:
                        current = self._resolve_tree_item_by_index(tree, locator, expanded=expanded)
                    except Exception:
                        window, tree = self._find_project_tree()
                        if (window.window_text(), int(window.process_id())) != window_identity:
                            raise RuntimeError(
                                "KV STUDIO window identity changed before rollback"
                            ) from None
                        current = self._resolve_tree_item_by_index(tree, locator, expanded=expanded)
                    after = current.window_text().strip()
                    if before == expected_source and after != expected_source:
                        rollback_attempted = True
                        self._commit_tree_item_text(window, tree, current, expected_source)
                        restored = self._wait_for_tree_text(
                            tree,
                            locator,
                            expected_source,
                            expanded=expanded,
                            timeout_seconds=2.0,
                        )
                        after = restored.window_text().strip()
                        rollback_succeeded = after == expected_source
            except Exception as rollback_exc:
                error = f"{error}; rollback failed: {type(rollback_exc).__name__}: {rollback_exc}"
            result = TreeItemRenameResult(
                False,
                before,
                after,
                rollback_attempted=rollback_attempted,
                rollback_succeeded=rollback_succeeded,
                error=error,
            )
        restoration_errors: list[str] = []
        for control in reversed(tuple(dict.fromkeys(expanded))):
            try:
                self._collapse_and_verify(control)
            except Exception as exc:
                restoration_errors.append(f"{type(exc).__name__}: {exc}")
        if restoration_errors:
            suffix = "expansion-state restoration failed: " + "; ".join(restoration_errors)
            error = f"{result.error}; {suffix}" if result.error else suffix
            result = TreeItemRenameResult(
                result.performed,
                result.before,
                result.after,
                result.rollback_attempted,
                result.rollback_succeeded,
                error,
            )
        return result

    def inspect_tree_item_menu(
        self,
        *,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        expected_source: str,
    ) -> TreeItemMenuInspection:
        """Snapshot one exact node's context menu without invoking any command."""
        window_title = ""
        process_id = 0
        menu_handle = 0
        menu_automation_id = ""
        menu_class_name = ""
        items: tuple[MenuItemSnapshot, ...] = ()
        warnings: list[str] = []
        expanded: list[object] = []
        menu = None
        right_click_sent = False
        menu_closed = False
        source_verified = False
        try:
            window, tree = self._find_project_tree()
            window_title = window.window_text()
            process_id = int(window.process_id())
            window_identity = (window_title, process_id)
            self._restore_and_focus_editor(window)
            native_tree = self._native_project_tree(window, tree)
            item = self._resolve_tree_item_for_edit(
                tree,
                locator,
                expected_path,
                expanded=expanded,
            )
            if item.window_text().strip() != expected_source:
                raise RuntimeError("source precondition failed")

            baseline = {
                self._menu_identity(candidate)
                for candidate in self._visible_same_process_menus(process_id)
            }
            native_tree.set_focus()
            item.select()
            if item.window_text().strip() != expected_source:
                raise RuntimeError("tree-item identity changed before context click")
            item.click_input(button="right")
            right_click_sent = True

            deadline = time.monotonic() + 2.0
            while menu is None:
                candidates = tuple(
                    candidate
                    for candidate in self._visible_same_process_menus(process_id)
                    if self._menu_identity(candidate) not in baseline
                )
                if len(candidates) == 1:
                    menu = candidates[0]
                    break
                if len(candidates) > 1:
                    raise RuntimeError("multiple new KV STUDIO context menus were found")
                if time.monotonic() >= deadline:
                    raise RuntimeError("KV STUDIO tree-item context menu was not found")
                time.sleep(0.05)

            info = menu.element_info
            menu_handle = int(getattr(info, "handle", 0) or 0)
            menu_automation_id = str(getattr(info, "automation_id", "") or "")
            menu_class_name = str(getattr(info, "class_name", "") or "")
            items = self._snapshot_menu_items(menu, process_id=process_id)
            self._send_tree_rename_key(menu, "{ESC}")
            close_deadline = time.monotonic() + 1.0
            menu_identity = self._menu_identity(menu)
            while any(
                self._menu_identity(candidate) == menu_identity
                for candidate in self._visible_same_process_menus(process_id)
            ):
                if time.monotonic() >= close_deadline:
                    raise RuntimeError("KV STUDIO context menu did not close after Escape")
                time.sleep(0.05)
            menu_closed = True

            current_window, current_tree = self._find_project_tree()
            if (current_window.window_text(), int(current_window.process_id())) != window_identity:
                raise RuntimeError("KV STUDIO window identity changed during menu inspection")
            current = self._resolve_tree_item_by_index(
                current_tree,
                locator,
                expanded=expanded,
            )
            source_verified = current.window_text().strip() == expected_source
            if not source_verified:
                raise RuntimeError("tree-item source changed during context-menu inspection")
        except Exception as exc:
            warnings.append(f"{type(exc).__name__}: {exc}")
        finally:
            if right_click_sent and not menu_closed:
                try:
                    target = menu if menu is not None else native_tree
                    self._send_tree_rename_key(target, "{ESC}")
                except Exception as exc:
                    warnings.append(f"menu close failed: {type(exc).__name__}: {exc}")
            for control in reversed(tuple(dict.fromkeys(expanded))):
                try:
                    self._collapse_and_verify(control)
                except Exception as exc:
                    warnings.append(
                        f"expansion-state restoration failed: {type(exc).__name__}: {exc}"
                    )

        complete = menu_closed and source_verified and bool(items) and not warnings
        return TreeItemMenuInspection(
            window_title=window_title,
            process_id=process_id,
            locator=locator,
            path=expected_path,
            source=expected_source,
            menu_native_handle=menu_handle,
            menu_automation_id=menu_automation_id,
            menu_class_name=menu_class_name,
            items=items,
            complete=complete,
            warnings=tuple(warnings),
        )

    def _find_project_tree(self):  # type: ignore[no-untyped-def]
        matches: list[tuple[object, object]] = []
        for window in self._allowed_windows():
            matches.extend((window, tree) for tree in self._project_trees(window))
        if not matches:
            raise RuntimeError(
                "KV STUDIO ProjectTreeView was not found; restore the editor window "
                "and open a project"
            )
        if len(matches) > 1:
            raise RuntimeError("multiple KV STUDIO project trees were found; keep one editor open")
        return matches[0]

    def _allowed_windows(self) -> tuple[object, ...]:
        matches: list[object] = []
        for window in self._desktop().windows():
            try:
                if self._is_allowed_title(window.window_text()):
                    matches.append(window)
            except Exception:
                # A transient/stale UIA top-level wrapper must not hide the live
                # allowlisted KV editor from a read-only status request.
                continue
        return tuple(matches)

    @staticmethod
    def _safe_control_flag(control, method_name: str) -> bool:  # type: ignore[no-untyped-def]
        try:
            method = getattr(control, method_name)
            return bool(method())
        except Exception:
            return False

    @staticmethod
    def _project_trees(window) -> tuple[object, ...]:  # type: ignore[no-untyped-def]
        try:
            descendants = window.descendants(control_type="Tree")
        except Exception:
            return ()
        matches: list[object] = []
        for candidate in descendants:
            if str(candidate.element_info.automation_id or "") != "ProjectTreeView":
                continue
            class_name = str(getattr(candidate.element_info, "class_name", "") or "")
            window_pid = int(window.process_id())
            tree_pid = int(getattr(candidate.element_info, "process_id", 0) or 0)
            if "SysTreeView32" in class_name and tree_pid in {0, window_pid}:
                matches.append(candidate)
        return tuple(matches)

    def _visible_same_process_menus(self, process_id: int) -> tuple[object, ...]:
        menus: list[object] = []
        for candidate in self._desktop().windows():
            try:
                info = candidate.element_info
                if (
                    str(getattr(info, "control_type", "")) == "Menu"
                    and int(getattr(info, "process_id", 0) or 0) == process_id
                    and candidate.is_visible()
                    and candidate.is_enabled()
                ):
                    menus.append(candidate)
            except Exception:
                continue
        return tuple(menus)

    @staticmethod
    def _menu_identity(menu) -> tuple[object, ...]:  # type: ignore[no-untyped-def]
        info = menu.element_info
        runtime_id = tuple(getattr(info, "runtime_id", ()) or ())
        if runtime_id:
            return ("runtime_id", *runtime_id)
        return ("handle", int(getattr(info, "handle", 0) or 0))

    @staticmethod
    def _snapshot_menu_items(menu, *, process_id: int) -> tuple[MenuItemSnapshot, ...]:  # type: ignore[no-untyped-def]
        snapshots: list[MenuItemSnapshot] = []
        for candidate in menu.descendants(control_type="MenuItem")[:128]:
            info = candidate.element_info
            if int(getattr(info, "process_id", 0) or 0) != process_id:
                continue
            if not candidate.is_visible():
                continue
            runtime_id = tuple(int(part) for part in (getattr(info, "runtime_id", ()) or ()))
            snapshots.append(
                MenuItemSnapshot(
                    text=candidate.window_text()[:512],
                    automation_id=str(getattr(info, "automation_id", "") or "")[:256],
                    class_name=str(getattr(info, "class_name", "") or "")[:256],
                    control_type=str(getattr(info, "control_type", "") or "")[:64],
                    native_handle=int(getattr(info, "handle", 0) or 0),
                    runtime_id=runtime_id[:32],
                    enabled=bool(candidate.is_enabled()),
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _is_minimized(window) -> bool:  # type: ignore[no-untyped-def]
        try:
            return bool(window.is_minimized())
        except Exception:
            try:
                return int(window.get_show_state()) == 2
            except Exception:
                return False

    def _restore_and_focus_editor(self, window) -> None:  # type: ignore[no-untyped-def]
        title = window.window_text()
        process_id = int(window.process_id())
        if not self._is_allowed_title(title):
            raise RuntimeError("refusing to activate a non-KV STUDIO window")
        if self._is_minimized(window):
            window.restore()
        window.set_focus()
        if int(window.process_id()) != process_id or window.window_text() != title:
            raise RuntimeError("KV STUDIO window identity changed during activation")

    def _resolve_tree_item_for_edit(
        self,
        tree,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
        *,
        expanded: list[object],
    ):  # type: ignore[no-untyped-def]
        if not locator or len(locator) != len(expected_path):
            raise RuntimeError("tree locator and expected path must have equal non-zero depth")
        current = tree
        for sibling_index, expected_name in zip(locator, expected_path, strict=True):
            if current is not tree and self._expand_state(current) == "collapsed":
                current.expand()
                if not self._wait_for_state(current, {"expanded"}, timeout_seconds=0.5):
                    raise RuntimeError("tree ancestor could not be expanded")
                expanded.append(current)
            children = self._tree_item_children(current)
            if sibling_index >= len(children):
                raise RuntimeError("project-tree locator no longer exists")
            current = children[sibling_index]
            if current.window_text().strip() != expected_name:
                raise RuntimeError("project-tree locator identity changed")
        return current

    def _resolve_tree_item_by_index(
        self,
        tree,
        locator: tuple[int, ...],
        *,
        expanded: list[object],
    ):  # type: ignore[no-untyped-def]
        if not locator:
            raise RuntimeError("tree locator cannot be empty")
        current = tree
        for sibling_index in locator:
            if current is not tree and self._expand_state(current) == "collapsed":
                current.expand()
                if not self._wait_for_state(current, {"expanded"}, timeout_seconds=0.5):
                    raise RuntimeError("tree ancestor could not be expanded")
                expanded.append(current)
            children = self._tree_item_children(current)
            if sibling_index >= len(children):
                raise RuntimeError("project-tree locator no longer exists")
            current = children[sibling_index]
        return current

    def _wait_for_tree_text(
        self,
        tree,
        locator: tuple[int, ...],
        expected_text: str,
        *,
        expanded: list[object],
        timeout_seconds: float,
    ):  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout_seconds
        last_text = ""
        while True:
            current = self._resolve_tree_item_by_index(tree, locator, expanded=expanded)
            last_text = current.window_text().strip()
            if last_text == expected_text:
                return current
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "tree item text did not settle to the exact expected value; "
                    f"actual={last_text!r}"
                )
            time.sleep(0.05)

    @staticmethod
    def _native_tree_wrapper(handle: int):  # type: ignore[no-untyped-def]
        """Create a native TreeView wrapper lazily for portable imports."""
        from pywinauto.controls.common_controls import TreeViewWrapper

        return TreeViewWrapper(handle)

    @staticmethod
    def _native_edit_wrapper(handle: int):  # type: ignore[no-untyped-def]
        """Create a native Edit wrapper with an exact-text setter."""
        from pywinauto.controls.win32_controls import EditWrapper

        return EditWrapper(handle)

    def _native_project_tree(self, window, tree):  # type: ignore[no-untyped-def]
        info = tree.element_info
        handle = int(getattr(info, "handle", 0) or 0)
        class_name = str(getattr(info, "class_name", "") or "")
        window_pid = int(window.process_id())
        tree_pid = int(getattr(info, "process_id", 0) or 0)
        if not handle or "SysTreeView32" not in class_name:
            raise RuntimeError("ProjectTreeView has no supported native TreeView handle")
        if tree_pid not in {0, window_pid}:
            raise RuntimeError("ProjectTreeView process identity does not match KV STUDIO")

        native_tree = self._native_tree_wrapper(handle)
        native_handle = int(getattr(native_tree, "handle", 0) or 0)
        if native_handle != handle or int(native_tree.process_id()) != window_pid:
            raise RuntimeError("native ProjectTreeView identity does not match KV STUDIO")
        return native_tree

    @staticmethod
    def _active_tree_edit_handle(native_tree) -> int:  # type: ignore[no-untyped-def]
        return int(native_tree.send_message(_TVM_GETEDITCONTROL, 0, 0) or 0)

    @staticmethod
    def _send_tree_rename_key(control, key: str) -> None:  # type: ignore[no-untyped-def]
        if key not in _TREE_RENAME_KEYS:
            raise RuntimeError("refusing non-allowlisted ProjectTreeView key input")
        control.type_keys(key, set_foreground=False)

    def _validated_tree_edit(
        self,
        *,
        edit_handle: int,
        native_tree,
        expected_process_id: int,
    ):  # type: ignore[no-untyped-def]
        edit = self._native_edit_wrapper(edit_handle)
        if int(getattr(edit, "handle", 0) or 0) != edit_handle:
            raise RuntimeError("native tree editor handle changed during activation")
        if int(edit.process_id()) != expected_process_id:
            raise RuntimeError("native tree editor process identity does not match KV STUDIO")
        parent = edit.parent()
        if int(getattr(parent, "handle", 0) or 0) != int(native_tree.handle):
            raise RuntimeError("native tree editor is not a child of ProjectTreeView")
        if str(edit.class_name() or "") != "Edit":
            raise RuntimeError("native ProjectTreeView editor has an unsupported window class")
        if not edit.is_visible() or not edit.is_enabled():
            raise RuntimeError("native ProjectTreeView editor is not ready for input")
        return edit

    def _cancel_tree_edit(
        self,
        native_tree,
        edit_handle: int,
        expected_process_id: int,
    ) -> None:  # type: ignore[no-untyped-def]
        """Cancel only the exact edit HWND activated by this operation."""
        try:
            if self._active_tree_edit_handle(native_tree) != edit_handle:
                return
            edit = self._validated_tree_edit(
                edit_handle=edit_handle,
                native_tree=native_tree,
                expected_process_id=expected_process_id,
            )
            self._send_tree_rename_key(edit, "{ESC}")
        except Exception:
            # The original error remains authoritative. Post-write verification and
            # the outer exact-source rollback still decide the operation result.
            return

    def _commit_tree_item_text(self, window, tree, item, target: str) -> None:  # type: ignore[no-untyped-def]
        """Use the exact native TreeView editor and only F2/Enter/Escape keys."""
        native_tree = self._native_project_tree(window, tree)
        window_pid = int(window.process_id())
        if self._active_tree_edit_handle(native_tree):
            raise RuntimeError("ProjectTreeView already has an active editor")

        # UIA resolves/selects the exact pinned item; native focus/key delivery is
        # required for the WindowsForms SysTreeView32 label editor used by KV.
        native_tree.set_focus()
        item.select()
        native_tree.set_focus()
        self._send_tree_rename_key(native_tree, "{F2}")

        edit_handle = 0
        deadline = time.monotonic() + 2.0
        while not edit_handle:
            edit_handle = self._active_tree_edit_handle(native_tree)
            if edit_handle:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("KV STUDIO native in-place tree editor was not found")
            time.sleep(0.05)

        try:
            edit = self._validated_tree_edit(
                edit_handle=edit_handle,
                native_tree=native_tree,
                expected_process_id=window_pid,
            )
            edit.set_focus()
            edit.set_edit_text(target)
            if edit.window_text() != target:
                raise RuntimeError("native tree editor did not accept the exact target text")
            self._send_tree_rename_key(edit, "{ENTER}")

            close_deadline = time.monotonic() + 2.0
            while self._active_tree_edit_handle(native_tree):
                if time.monotonic() >= close_deadline:
                    raise RuntimeError("native tree editor remained active after commit")
                time.sleep(0.05)
        except Exception:
            self._cancel_tree_edit(native_tree, edit_handle, window_pid)
            raise

    def _crawl_project_node(
        self,
        control,
        *,
        parent_path: tuple[str, ...],
        parent_locator: tuple[int, ...],
        sibling_index: int,
        depth: int,
        expand_all: bool,
        budget: list[int],
        expansion_budget: list[int],
        deadline: float,
        expanded: list[tuple[tuple[int, ...], tuple[str, ...]]],
        warnings: list[str],
        counters: dict[str, int],
        truncated: list[bool],
    ) -> ProjectTreeNodeSnapshot:  # type: ignore[no-untyped-def]
        name = control.window_text().strip()
        path = (*parent_path, name)
        locator = (*parent_locator, sibling_index)
        budget[0] -= 1
        counters["items"] += 1
        initial_state = self._expand_state(control)
        expanded_here = False
        expansion_attempted = False

        if time.monotonic() >= deadline:
            truncated[0] = True
            warnings.append(f"project-tree time limit reached at: {' > '.join(path)}")
            return ProjectTreeNodeSnapshot(
                name=name,
                path=path,
                depth=depth,
                sibling_index=sibling_index,
                locator=locator,
                initial_expansion_state=initial_state,
                visible=bool(control.is_visible()),
                truncated=True,
            )

        if expand_all and initial_state == "collapsed":
            if expansion_budget[0] <= 0:
                truncated[0] = True
                warnings.append(f"project-tree expansion limit reached at: {' > '.join(path)}")
            else:
                expansion_budget[0] -= 1
                expansion_attempted = True
                try:
                    control.expand()
                except Exception:
                    truncated[0] = True
                    warnings.append(f"could not expand tree item: {' > '.join(path)}")
            if expansion_attempted and self._wait_for_state(
                control,
                {"expanded", "partially_expanded"},
                timeout_seconds=0.5,
            ):
                expanded_here = True
                expanded.append((locator, path))
                if self._expand_state(control) != "expanded":
                    truncated[0] = True
                    warnings.append(f"tree item only partially expanded: {' > '.join(path)}")
                if self._expansion_settle_seconds:
                    time.sleep(self._expansion_settle_seconds)
            elif expansion_attempted:
                truncated[0] = True
                warnings.append(f"tree item remained collapsed: {' > '.join(path)}")

        if expand_all and initial_state == "unknown":
            truncated[0] = True
            warnings.append(f"tree item has unknown expansion state: {' > '.join(path)}")

        if initial_state == "partially_expanded":
            truncated[0] = True
            warnings.append(f"tree item was initially partially expanded: {' > '.join(path)}")

        if not expand_all and initial_state == "collapsed":
            truncated[0] = True
            warnings.append(f"collapsed tree item was not inventoried: {' > '.join(path)}")

        enumeration_failed = False
        try:
            child_controls = (
                self._settled_tree_item_children(control, deadline=deadline)
                if expanded_here
                else self._tree_item_children(control)
            )
        except Exception:
            child_controls = ()
            enumeration_failed = True
            truncated[0] = True
            warnings.append(f"could not enumerate tree item children: {' > '.join(path)}")
        expanded_container = expanded_here or initial_state in {
            "expanded",
            "partially_expanded",
        }
        missing_expanded_children = expanded_container and not child_controls
        depth_limited = depth >= self._max_project_depth and bool(child_controls)
        budget_limited = budget[0] <= 0 and bool(child_controls)
        node_truncated = (
            depth_limited
            or budget_limited
            or missing_expanded_children
            or enumeration_failed
            or (not expand_all and initial_state == "collapsed")
            or (expand_all and initial_state == "unknown")
            or initial_state == "partially_expanded"
        )
        if missing_expanded_children:
            truncated[0] = True
            warnings.append(f"expanded tree item exposed no children: {' > '.join(path)}")
        if node_truncated:
            truncated[0] = True
            children: tuple[ProjectTreeNodeSnapshot, ...] = ()
        else:
            child_nodes: list[ProjectTreeNodeSnapshot] = []
            for child_index, child in enumerate(child_controls):
                if budget[0] <= 0 or time.monotonic() >= deadline:
                    truncated[0] = True
                    warnings.append(
                        f"project-tree inventory limit reached below: {' > '.join(path)}"
                    )
                    break
                child_nodes.append(
                    self._crawl_project_node(
                        child,
                        parent_path=path,
                        parent_locator=locator,
                        sibling_index=child_index,
                        depth=depth + 1,
                        expand_all=expand_all,
                        budget=budget,
                        expansion_budget=expansion_budget,
                        deadline=deadline,
                        expanded=expanded,
                        warnings=warnings,
                        counters=counters,
                        truncated=truncated,
                    )
                )
            children = tuple(child_nodes)
        return ProjectTreeNodeSnapshot(
            name=name,
            path=path,
            depth=depth,
            sibling_index=sibling_index,
            locator=locator,
            initial_expansion_state=initial_state,
            expanded_for_inventory=expanded_here,
            visible=bool(control.is_visible()),
            truncated=node_truncated,
            children=children,
        )

    @staticmethod
    def _tree_item_children(control) -> tuple[object, ...]:  # type: ignore[no-untyped-def]
        children = control.children()
        return tuple(
            child for child in children if str(child.element_info.control_type) == "TreeItem"
        )

    def _settled_tree_item_children(self, control, *, deadline: float) -> tuple[object, ...]:  # type: ignore[no-untyped-def]
        previous_identity: tuple[tuple[object, ...], ...] | None = None
        current: tuple[object, ...] = ()
        settle_deadline = min(
            deadline,
            time.monotonic() + max(0.25, self._expansion_settle_seconds * 10),
        )
        while time.monotonic() < settle_deadline:
            current = self._tree_item_children(control)
            current_identity = tuple(self._tree_item_identity(child) for child in current)
            if current and current_identity == previous_identity:
                return current
            previous_identity = current_identity
            if self._expansion_settle_seconds:
                time.sleep(self._expansion_settle_seconds)
            else:
                time.sleep(0.01)
        return current

    @staticmethod
    def _tree_item_identity(control) -> tuple[object, ...]:  # type: ignore[no-untyped-def]
        info = control.element_info
        runtime_id = getattr(info, "runtime_id", None)
        if runtime_id:
            return ("runtime_id", *tuple(runtime_id))
        return (
            "fallback",
            control.window_text().strip(),
            str(getattr(info, "control_type", "")),
            int(getattr(info, "handle", 0) or 0),
        )

    def _resolve_tree_item(
        self,
        tree,
        locator: tuple[int, ...],
        expected_path: tuple[str, ...],
    ):  # type: ignore[no-untyped-def]
        current = tree
        for sibling_index, expected_name in zip(locator, expected_path, strict=True):
            children = self._tree_item_children(current)
            if sibling_index >= len(children):
                raise RuntimeError("project-tree locator no longer exists")
            current = children[sibling_index]
            if current.window_text().strip() != expected_name:
                raise RuntimeError("project-tree locator identity changed")
        return current

    @staticmethod
    def _expand_state(control) -> str:  # type: ignore[no-untyped-def]
        try:
            return _EXPANSION_STATES.get(int(control.get_expand_state()), "unknown")
        except Exception:
            return "unknown"

    def _wait_for_state(
        self,
        control,
        expected: set[str],
        *,
        timeout_seconds: float,
    ) -> bool:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout_seconds
        while True:
            if self._expand_state(control) in expected:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(self._expansion_settle_seconds, 0.01))

    def _collapse_and_verify(self, control) -> None:  # type: ignore[no-untyped-def]
        control.collapse()
        if not self._wait_for_state(control, {"collapsed"}, timeout_seconds=0.5):
            raise RuntimeError("tree item did not return to collapsed state")

    def _desktop(self):  # type: ignore[no-untyped-def]
        if __import__("platform").system() != "Windows":
            raise RuntimeError("KV STUDIO UI automation is available only on Windows")
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise RuntimeError("Install pywinauto to use the KV STUDIO UI adapter") from exc
        return Desktop(backend="uia")

    def _snapshot_children(
        self, control, *, depth: int, budget: list[int]
    ) -> tuple[ControlSnapshot, ...]:  # type: ignore[no-untyped-def]
        if depth >= self._max_depth or budget[0] <= 0:
            return ()
        try:
            live_children = control.children()
        except Exception:  # COM/UIA controls can disappear during enumeration.
            return ()
        snapshots: list[ControlSnapshot] = []
        for child in live_children:
            if budget[0] <= 0:
                break
            snapshots.append(self._snapshot(child, depth=depth, budget=budget))
        return tuple(snapshots)

    def _snapshot(self, control, *, depth: int, budget: list[int]) -> ControlSnapshot:  # type: ignore[no-untyped-def]
        budget[0] -= 1
        info = control.element_info
        rectangle = getattr(info, "rectangle", None)
        bounds = None
        if rectangle is not None:
            bounds = (
                int(rectangle.left),
                int(rectangle.top),
                int(rectangle.right),
                int(rectangle.bottom),
            )
        children = self._snapshot_children(control, depth=depth + 1, budget=budget)
        return ControlSnapshot(
            name=control.window_text(),
            control_type=str(info.control_type),
            automation_id=str(info.automation_id or ""),
            class_name=str(getattr(info, "class_name", "") or ""),
            framework_id=str(getattr(info, "framework_id", "") or ""),
            native_handle=int(getattr(info, "handle", 0) or 0),
            rectangle=bounds,
            enabled=bool(control.is_enabled()),
            visible=bool(control.is_visible()),
            truncated=depth + 1 >= self._max_depth or budget[0] <= 0,
            children=children,
        )

    def _is_allowed_title(self, title: str) -> bool:
        # The main editor title does not reliably contain the product version;
        # version 11.62 is validated separately by the doctor/probe workflow.
        browser_suffix = re.compile(r"(?:Microsoft\s*Edge|Google Chrome|Mozilla Firefox)$", re.I)
        product_marker = re.compile(r"\bKV STUDIO\b", re.I)
        # Browser titles can contain zero-width format characters (for example
        # ``Microsoft\u200b Edge``), which must not bypass the suffix denylist.
        normalized_title = "".join(
            character for character in title if unicodedata.category(character) != "Cf"
        )
        # Configuration may narrow the title match, but can never broaden it to
        # non-KV windows.
        return (
            bool(product_marker.search(normalized_title))
            and bool(self._title_pattern.search(normalized_title))
            and not browser_suffix.search(normalized_title)
        )
