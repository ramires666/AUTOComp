"""Adapters isolate optional Windows UI Automation dependencies."""

from __future__ import annotations

import re
import time
from typing import Protocol

from .models import (
    ControlSnapshot,
    ProjectTreeInventory,
    ProjectTreeNodeSnapshot,
    WindowSnapshot,
)

_EXPANSION_STATES = {
    0: "collapsed",
    1: "expanded",
    2: "partially_expanded",
    3: "leaf",
}


class KVStudioAdapter(Protocol):
    """Minimal adapter surface; implementations must never access a PLC."""

    def discover(self) -> tuple[WindowSnapshot, ...]:
        """Return only KV STUDIO 11.62 windows."""

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        """Expand a tree item in the already allowlisted local editor window."""

    def inventory_project_tree(
        self, *, expand_all: bool, restore_state: bool
    ) -> ProjectTreeInventory:
        """Inventory the native project tree with optional reversible expansion."""


class FakeKVStudioAdapter:
    """Deterministic adapter for tests; it has no OS or network side effects."""

    def __init__(self, windows: tuple[WindowSnapshot, ...] = ()) -> None:
        self.windows = windows
        self.expanded_paths: list[tuple[str, ...]] = []
        self.project_tree_inventory = ProjectTreeInventory(
            "KV STUDIO", 0, "ProjectTreeView", 0, 0, 0, True
        )

    def discover(self) -> tuple[WindowSnapshot, ...]:
        return self.windows

    def expand_tree_item(self, target_path: tuple[str, ...]) -> bool:
        self.expanded_paths.append(target_path)
        return bool(target_path)

    def inventory_project_tree(
        self, *, expand_all: bool, restore_state: bool
    ) -> ProjectTreeInventory:
        del expand_all, restore_state
        return self.project_tree_inventory


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
            warnings.append(
                "project-tree traversal failed: "
                f"{type(exc).__name__}: {exc}"
            )
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

    def _find_project_tree(self):  # type: ignore[no-untyped-def]
        matches: list[tuple[object, object]] = []
        for window in self._desktop().windows():
            title = window.window_text()
            if not self._is_allowed_title(title):
                continue
            try:
                descendants = window.descendants(control_type="Tree")
            except Exception:
                continue
            for candidate in descendants:
                if str(candidate.element_info.automation_id or "") == "ProjectTreeView":
                    class_name = str(getattr(candidate.element_info, "class_name", "") or "")
                    window_pid = int(window.process_id())
                    tree_pid = int(getattr(candidate.element_info, "process_id", 0) or 0)
                    same_process = tree_pid in {0, window_pid}
                    if "SysTreeView32" in class_name and same_process:
                        matches.append((window, candidate))
        if not matches:
            raise RuntimeError(
                "KV STUDIO ProjectTreeView was not found; restore the editor window "
                "and open a project"
            )
        if len(matches) > 1:
            raise RuntimeError("multiple KV STUDIO project trees were found; keep one editor open")
        return matches[0]

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
            warnings.append(
                "expanded tree item exposed no children: " f"{' > '.join(path)}"
            )
        if node_truncated:
            truncated[0] = True
            children: tuple[ProjectTreeNodeSnapshot, ...] = ()
        else:
            child_nodes: list[ProjectTreeNodeSnapshot] = []
            for child_index, child in enumerate(child_controls):
                if budget[0] <= 0 or time.monotonic() >= deadline:
                    truncated[0] = True
                    warnings.append(
                        "project-tree inventory limit reached below: "
                        f"{' > '.join(path)}"
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
            child
            for child in children
            if str(child.element_info.control_type) == "TreeItem"
        )

    def _settled_tree_item_children(
        self, control, *, deadline: float
    ) -> tuple[object, ...]:  # type: ignore[no-untyped-def]
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
        return bool(self._title_pattern.search(title)) and not browser_suffix.search(title)
