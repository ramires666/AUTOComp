"""Policy layer enforcing dry-run and the UI action allowlist."""

from __future__ import annotations

import re
import time
from typing import Protocol

from autocomp.desktop import DesktopClipboardText, DesktopFrame, DesktopWindow

from .adapter import KVStudioAdapter
from .models import ActionKind, ActionRequest, ActionResult


class DesktopAdapter(Protocol):
    """App-agnostic eyes/hands surface with pinned window identity only."""

    def enumerate_windows(self) -> tuple[DesktopWindow, ...]: ...

    def snapshot(
        self,
        *,
        handle: int,
        expected_pid: int,
        expected_title: str,
    ) -> DesktopFrame: ...

    def clipboard_text(
        self,
        *,
        handle: int,
        expected_pid: int,
        expected_title: str,
    ) -> DesktopClipboardText: ...

    def input(
        self,
        *,
        handle: int,
        expected_pid: int,
        expected_title: str,
        operation: str,
        x: int | None,
        y: int | None,
        delta: int | None,
        text: str,
    ) -> bool: ...


class DesktopWorker:
    """Offline desktop worker with an optional legacy KV STUDIO adapter."""

    def __init__(
        self,
        adapter: KVStudioAdapter | None,
        *,
        apply_enabled: bool = False,
        desktop_adapter: DesktopAdapter | None = None,
    ) -> None:
        self._adapter = adapter
        self._apply_enabled = apply_enabled
        self._desktop_adapter = desktop_adapter

    @property
    def desktop_available(self) -> bool:
        """Whether universal desktop actions were explicitly wired at startup."""
        return self._desktop_adapter is not None

    @property
    def application_adapter_available(self) -> bool:
        """Whether the optional KV-specific acceleration layer was enabled."""
        return self._adapter is not None

    def execute(self, request: ActionRequest) -> ActionResult:
        if request.kind is ActionKind.DESKTOP_WINDOWS:
            desktop = self._require_desktop_adapter()
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Desktop window inventory collected (read-only).",
                audit={"mode": "dry-run", "operation": "desktop_windows"},
                desktop_windows=desktop.enumerate_windows(),
            )
        if request.kind is ActionKind.DESKTOP_SNAPSHOT:
            desktop = self._require_desktop_adapter()
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Pinned desktop window snapshot captured (read-only).",
                audit={"mode": "dry-run", "operation": "desktop_snapshot"},
                desktop_snapshot=desktop.snapshot(
                    handle=request.window_handle,
                    expected_pid=request.expected_pid,
                    expected_title=request.expected_title,
                ),
            )
        if request.kind is ActionKind.DESKTOP_CLIPBOARD_TEXT:
            desktop = self._require_desktop_adapter()
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Pinned process Unicode clipboard text collected (read-only).",
                audit={"mode": "dry-run", "operation": "desktop_clipboard_text"},
                desktop_clipboard_text=desktop.clipboard_text(
                    handle=request.window_handle,
                    expected_pid=request.expected_pid,
                    expected_title=request.expected_title,
                ),
            )
        if request.kind is ActionKind.DESKTOP_INPUT:
            return self._desktop_input(request)
        if request.kind is ActionKind.DESKTOP_INPUT_SEQUENCE:
            return self._desktop_input_sequence(request)
        if self._adapter is None:
            raise ValueError("application-specific adapter is disabled")
        if request.kind is ActionKind.STATUS:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="KV STUDIO window status collected (read-only).",
                audit={"mode": "dry-run", "operation": "status"},
                window_state=self._adapter.status(),
            )
        if request.kind is ActionKind.INVENTORY:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Inventory snapshot collected (read-only).",
                windows=self._adapter.discover(),
                audit={"mode": "dry-run", "operation": "inventory"},
            )
        if request.kind is ActionKind.EXPAND_TREE_ITEM:
            return self._expand_tree_item(request)
        if request.kind is ActionKind.INVENTORY_PROJECT_TREE:
            return self._inventory_project_tree(request)
        if request.kind is ActionKind.ACTIVATE_TREE_ITEM:
            return self._activate_tree_item(request)
        if request.kind is ActionKind.RENAME_TREE_ITEM:
            return self._rename_tree_item(request, probe=False)
        if request.kind is ActionKind.PROBE_TREE_ITEM_RENAME:
            return self._rename_tree_item(request, probe=True)
        if request.kind is ActionKind.INSPECT_TREE_ITEM_MENU:
            return self._inspect_tree_item_menu(request)
        if request.kind is ActionKind.VISUAL_SNAPSHOT:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="KV STUDIO visual snapshot captured (read-only).",
                audit={"mode": "dry-run", "operation": "visual_snapshot"},
                visual_snapshot=self._adapter.visual_snapshot(),
            )
        if request.kind is ActionKind.VISUAL_INPUT:
            return self._visual_input(request)
        raise ValueError(f"Unsupported UI action: {request.kind!r}")

    def _desktop_input(self, request: ActionRequest) -> ActionResult:
        desktop = self._require_desktop_adapter()
        if request.desktop_operation is None:
            raise ValueError("desktop input operation is required")
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Dry-run: desktop input was validated but not performed.",
                audit={"mode": "dry-run", "operation": request.desktop_operation.value},
            )
        self._require_apply(request)
        performed = desktop.input(
            handle=request.window_handle,
            expected_pid=request.expected_pid,
            expected_title=request.expected_title,
            operation=request.desktop_operation.value,
            x=request.x,
            y=request.y,
            delta=request.delta,
            text=request.text,
        )
        return ActionResult(
            kind=request.kind,
            performed=performed,
            message=(
                "Pinned desktop input performed."
                if performed
                else "Pinned desktop input was not performed."
            ),
            audit={
                "mode": "apply",
                "checkpoint": request.checkpoint,
                "operation": request.desktop_operation.value,
            },
        )

    def _desktop_input_sequence(self, request: ActionRequest) -> ActionResult:
        desktop = self._require_desktop_adapter()
        if not 1 <= len(request.desktop_operations) <= 8:
            raise ValueError("desktop input sequence requires 1 to 8 operations")
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Dry-run: desktop input sequence was validated but not performed.",
                audit={
                    "mode": "dry-run",
                    "operation": "desktop_input_sequence",
                    "operation_count": str(len(request.desktop_operations)),
                },
            )
        self._require_apply(request)
        completed_count = 0
        atomic_sequence = getattr(desktop, "input_sequence", None)
        if callable(atomic_sequence):
            completed_count = atomic_sequence(
                handle=request.window_handle,
                expected_pid=request.expected_pid,
                expected_title=request.expected_title,
                operations=tuple(
                    {
                        "operation": step.operation.value,
                        "x": step.x,
                        "y": step.y,
                        "delta": step.delta,
                        "text": step.text,
                        "pause_ms": step.pause_ms,
                    }
                    for step in request.desktop_operations
                ),
            )
            if completed_count != len(request.desktop_operations):
                return ActionResult(
                    kind=request.kind,
                    performed=False,
                    message="Pinned desktop input sequence stopped after an unperformed step.",
                    audit={
                        "mode": "apply",
                        "checkpoint": request.checkpoint,
                        "operation": "desktop_input_sequence",
                        "operation_count": str(len(request.desktop_operations)),
                        "completed_count": str(completed_count),
                    },
                )
            return ActionResult(
                kind=request.kind,
                performed=True,
                message="Pinned desktop input sequence performed.",
                audit={
                    "mode": "apply",
                    "checkpoint": request.checkpoint,
                    "operation": "desktop_input_sequence",
                    "operation_count": str(len(request.desktop_operations)),
                    "completed_count": str(completed_count),
                },
            )
        for index, step in enumerate(request.desktop_operations):
            performed = desktop.input(
                handle=request.window_handle,
                expected_pid=request.expected_pid,
                expected_title=request.expected_title,
                operation=step.operation.value,
                x=step.x,
                y=step.y,
                delta=step.delta,
                text=step.text,
            )
            if not performed:
                return ActionResult(
                    kind=request.kind,
                    performed=False,
                    message="Pinned desktop input sequence stopped after an unperformed step.",
                    audit={
                        "mode": "apply",
                        "checkpoint": request.checkpoint,
                        "operation": "desktop_input_sequence",
                        "operation_count": str(len(request.desktop_operations)),
                        "completed_count": str(completed_count),
                    },
                )
            completed_count += 1
            if index + 1 < len(request.desktop_operations) and step.pause_ms:
                time.sleep(step.pause_ms / 1000)
        return ActionResult(
            kind=request.kind,
            performed=True,
            message="Pinned desktop input sequence performed.",
            audit={
                "mode": "apply",
                "checkpoint": request.checkpoint,
                "operation": "desktop_input_sequence",
                "operation_count": str(len(request.desktop_operations)),
                "completed_count": str(completed_count),
            },
        )

    def _require_desktop_adapter(self) -> DesktopAdapter:
        if self._desktop_adapter is None:
            raise ValueError("desktop automation is not configured")
        return self._desktop_adapter

    def _expand_tree_item(self, request: ActionRequest) -> ActionResult:
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Dry-run: tree item was not expanded.",
                audit={"mode": "dry-run", "operation": "expand_tree_item"},
            )
        self._require_apply(request)
        if not request.target_path or any(not part.strip() for part in request.target_path):
            raise ValueError("A non-empty tree-item path is required")
        performed = self._adapter.expand_tree_item(request.target_path)
        return ActionResult(
            kind=request.kind,
            performed=performed,
            message="Tree item expanded." if performed else "Tree item could not be expanded.",
            audit={
                "mode": "apply",
                "checkpoint": request.checkpoint,
                "operation": "expand_tree_item",
            },
        )

    def _inventory_project_tree(self, request: ActionRequest) -> ActionResult:
        if not request.restore_state:
            raise ValueError("project-tree inventory must restore temporary expansion state")
        if request.expand_all:
            if not request.apply:
                raise ValueError("expand_all requires explicit apply mode")
            self._require_apply(request)
        elif request.apply or request.checkpoint:
            raise ValueError("apply and checkpoint are valid only when expand_all is enabled")
        inventory = self._adapter.inventory_project_tree(
            expand_all=request.expand_all,
            restore_state=True,
        )
        mode = "apply" if request.expand_all else "dry-run"
        return ActionResult(
            kind=request.kind,
            performed=request.expand_all and inventory.expanded_count > 0,
            message="Project-tree inventory collected with expansion state restored.",
            project_tree_inventory=inventory,
            audit={
                "mode": mode,
                "operation": "inventory_project_tree",
                "checkpoint": request.checkpoint if request.expand_all else "",
                "complete": str(inventory.complete).lower(),
                "item_count": str(inventory.item_count),
            },
        )

    def _rename_tree_item(self, request: ActionRequest, *, probe: bool) -> ActionResult:
        self._validate_rename_request(request)
        operation = "probe_tree_item_rename" if probe else "rename_tree_item"
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message=f"Dry-run: {operation} was validated but not performed.",
                audit={"mode": "dry-run", "operation": operation},
                before=request.expected_source,
                after=request.expected_source,
            )
        self._require_apply(request)
        forward = self._adapter.rename_tree_item(
            locator=request.locator,
            expected_path=request.expected_path,
            expected_source=request.expected_source,
            target=request.target,
        )
        if not probe or not forward.performed:
            audit = {
                "mode": "apply",
                "checkpoint": request.checkpoint,
                "operation": operation,
            }
            if forward.error:
                audit["error"] = forward.error
            if not forward.performed:
                message = f"Tree item was not renamed: {forward.error}"
            elif forward.error:
                message = (
                    f"Tree item renamed and verified; UI state restoration warning: {forward.error}"
                )
            else:
                message = "Tree item renamed and verified."
            return ActionResult(
                kind=request.kind,
                performed=forward.performed,
                message=message,
                audit=audit,
                before=forward.before,
                after=forward.after,
                rollback_attempted=forward.rollback_attempted,
                rollback_succeeded=forward.rollback_succeeded,
            )

        reverse = self._adapter.rename_tree_item(
            locator=request.locator,
            expected_path=(*request.expected_path[:-1], request.target),
            expected_source=request.target,
            target=request.expected_source,
        )
        restored = reverse.performed and reverse.after == request.expected_source
        audit = {
            "mode": "apply",
            "checkpoint": request.checkpoint,
            "operation": operation,
        }
        warnings = "; ".join(error for error in (forward.error, reverse.error) if error)
        if warnings:
            audit["error"] = warnings
        if not restored:
            message = f"Rename candidate was accepted but restoration failed: {reverse.error}"
        elif warnings:
            message = (
                "Rename candidate accepted and source restored; UI state restoration "
                f"warning: {warnings}"
            )
        else:
            message = "Rename candidate accepted; original text restored and verified."
        return ActionResult(
            kind=request.kind,
            performed=restored,
            message=message,
            audit=audit,
            before=forward.before,
            after=reverse.after,
            rollback_attempted=True,
            rollback_succeeded=restored,
        )

    def _activate_tree_item(self, request: ActionRequest) -> ActionResult:
        self._validate_tree_item_precondition(request)
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Dry-run: tree-item activation was validated but not performed.",
                audit={"mode": "dry-run", "operation": "activate_tree_item"},
                before=request.expected_source,
                after=request.expected_source,
            )
        self._require_apply(request)
        performed = self._adapter.activate_tree_item(
            locator=request.locator,
            expected_path=request.expected_path,
            expected_source=request.expected_source,
        )
        audit = {
            "mode": "apply",
            "checkpoint": request.checkpoint,
            "operation": "activate_tree_item",
        }
        visual_snapshot = None
        if performed:
            try:
                visual_snapshot = self._adapter.visual_snapshot()
            except Exception:
                # Activation has already happened. Preserve that successful outcome
                # so callers do not retry a double-click solely because capture failed.
                audit["snapshot"] = "unavailable"
        if not performed:
            message = "Tree item was not activated: source precondition failed."
        elif visual_snapshot is None:
            message = "Exact tree item activated; follow-up snapshot was unavailable."
        else:
            message = "Exact tree item activated."
        return ActionResult(
            kind=request.kind,
            performed=performed,
            message=message,
            audit=audit,
            before=request.expected_source,
            after=request.expected_source,
            visual_snapshot=visual_snapshot,
        )

    def _inspect_tree_item_menu(self, request: ActionRequest) -> ActionResult:
        self._validate_tree_item_precondition(request)
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Dry-run: context-menu inspection was validated but not performed.",
                audit={"mode": "dry-run", "operation": "inspect_tree_item_menu"},
            )
        self._require_apply(request)
        inspection = self._adapter.inspect_tree_item_menu(
            locator=request.locator,
            expected_path=request.expected_path,
            expected_source=request.expected_source,
        )
        return ActionResult(
            kind=request.kind,
            performed=inspection.complete,
            message=(
                "Tree-item context menu inspected and closed."
                if inspection.complete
                else "Tree-item context menu inspection was incomplete."
            ),
            audit={
                "mode": "apply",
                "checkpoint": request.checkpoint,
                "operation": "inspect_tree_item_menu",
                "complete": str(inspection.complete).lower(),
                "item_count": str(len(inspection.items)),
            },
            tree_item_menu_inspection=inspection,
        )

    def _visual_input(self, request: ActionRequest) -> ActionResult:
        if request.operation is None:
            raise ValueError("visual input operation is required")
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Dry-run: visual input was validated but not performed.",
                audit={"mode": "dry-run", "operation": request.operation.value},
            )
        self._require_apply(request)
        performed = self._adapter.visual_input(
            request.operation,
            x=request.x,
            y=request.y,
            delta=request.delta,
            text=request.text,
        )
        return ActionResult(
            kind=request.kind,
            performed=performed,
            message="Constrained KV STUDIO visual input performed.",
            audit={
                "mode": "apply",
                "checkpoint": request.checkpoint,
                "operation": request.operation.value,
            },
        )

    def _require_apply(self, request: ActionRequest) -> None:
        if not self._apply_enabled:
            raise ValueError("apply mode is disabled by worker safety configuration")
        if not request.apply:
            raise ValueError("explicit apply mode is required")
        checkpoint = request.checkpoint
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", checkpoint):
            raise ValueError("a safe explicit named checkpoint is required for apply mode")

    @staticmethod
    def _validate_rename_request(request: ActionRequest) -> None:
        DesktopWorker._validate_tree_item_precondition(request)
        for field_name, value in (("target", request.target),):
            if not value or value != value.strip() or len(value) > 512:
                raise ValueError(f"{field_name} must be non-empty, trimmed, and at most 512 chars")
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError(f"{field_name} contains control characters")
        if request.target == request.expected_source:
            raise ValueError("target must differ from expected_source")
        if any("\u3400" <= character <= "\u9fff" for character in request.target):
            raise ValueError("target must not contain CJK ideographs")

    @staticmethod
    def _validate_tree_item_precondition(request: ActionRequest) -> None:
        if not request.locator or len(request.locator) != len(request.expected_path):
            raise ValueError("locator and expected_path must have equal non-zero depth")
        if any(index < 0 for index in request.locator):
            raise ValueError("locator indices must be non-negative")
        if not request.expected_path or any(not part.strip() for part in request.expected_path):
            raise ValueError("expected_path must contain non-empty parts")
        if request.expected_path[-1] != request.expected_source:
            raise ValueError("expected_path leaf must exactly equal expected_source")
        for field_name, value in (("expected_source", request.expected_source),):
            if not value or value != value.strip() or len(value) > 512:
                raise ValueError(f"{field_name} must be non-empty, trimmed, and at most 512 chars")
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError(f"{field_name} contains control characters")


# Backward-compatible name for the deterministic KV-specific CLI/tests.
KVStudioWorker = DesktopWorker
