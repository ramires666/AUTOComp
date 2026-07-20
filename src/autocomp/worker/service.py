"""Policy layer enforcing dry-run and the UI action allowlist."""

from __future__ import annotations

from .adapter import KVStudioAdapter
from .models import ActionKind, ActionRequest, ActionResult


class KVStudioWorker:
    """Offline worker for inspecting, never programming, the editor UI."""

    def __init__(self, adapter: KVStudioAdapter) -> None:
        self._adapter = adapter

    def execute(self, request: ActionRequest) -> ActionResult:
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
        raise ValueError(f"Unsupported UI action: {request.kind!r}")

    def _expand_tree_item(self, request: ActionRequest) -> ActionResult:
        if not request.apply:
            return ActionResult(
                kind=request.kind,
                performed=False,
                message="Dry-run: tree item was not expanded.",
                audit={"mode": "dry-run", "operation": "expand_tree_item"},
            )
        if not request.checkpoint.strip():
            raise ValueError("An explicit named checkpoint is required for apply mode")
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
