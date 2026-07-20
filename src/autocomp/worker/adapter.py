"""Adapters isolate optional Windows UI Automation dependencies."""

from __future__ import annotations

import re
from typing import Protocol

from .models import ControlSnapshot, WindowSnapshot


class KVStudioAdapter(Protocol):
    """Minimal adapter surface; implementations must never access a PLC."""

    def discover(self) -> tuple[WindowSnapshot, ...]:
        """Return only KV STUDIO 11.62 windows."""

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        """Expand a tree item in the already allowlisted local editor window."""


class FakeKVStudioAdapter:
    """Deterministic adapter for tests; it has no OS or network side effects."""

    def __init__(self, windows: tuple[WindowSnapshot, ...] = ()) -> None:
        self.windows = windows
        self.expanded_paths: list[tuple[str, ...]] = []

    def discover(self) -> tuple[WindowSnapshot, ...]:
        return self.windows

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        self.expanded_paths.append(target_path)
        return bool(target_path)


class PywinautoKVStudioAdapter:
    """Read-only discovery scaffold for a locally running KV STUDIO 11.62.

    ``pywinauto`` is imported only inside methods so non-Windows inventory and
    translation workflows can import this package safely.
    """

    def __init__(
        self,
        title_pattern: str = r"\bKV STUDIO\b",
        *,
        max_depth: int = 12,
        max_controls: int = 5000,
    ) -> None:
        if max_depth < 1 or max_controls < 1:
            raise ValueError("UI inventory limits must be positive")
        self._title_pattern = re.compile(title_pattern, re.IGNORECASE)
        self._max_depth = max_depth
        self._max_controls = max_controls

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

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        """Scaffold only: tree expansion is intentionally not implemented yet."""
        del target_path
        return False

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
        return bool(self._title_pattern.search(title))
