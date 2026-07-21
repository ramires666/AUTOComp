"""Policy layer enforcing dry-run and the UI action allowlist."""

from __future__ import annotations

import re

from .adapter import KVStudioAdapter
from .models import ActionKind, ActionRequest, ActionResult


class KVStudioWorker:
    """Offline worker for inspecting, never programming, the editor UI."""

    def __init__(self, adapter: KVStudioAdapter, *, apply_enabled: bool = False) -> None:
        self._adapter = adapter
        self._apply_enabled = apply_enabled

    def execute(self, request: ActionRequest) -> ActionResult:
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
        if request.kind is ActionKind.RENAME_TREE_ITEM:
            return self._rename_tree_item(request, probe=False)
        if request.kind is ActionKind.PROBE_TREE_ITEM_RENAME:
            return self._rename_tree_item(request, probe=True)
        if request.kind is ActionKind.INSPECT_TREE_ITEM_MENU:
            return self._inspect_tree_item_menu(request)
        raise ValueError(f"Unsupported UI action: {request.kind!r}")

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
        KVStudioWorker._validate_tree_item_precondition(request)
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
