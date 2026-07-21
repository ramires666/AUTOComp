"""Safe, offline-only Windows UI automation primitives for KV STUDIO."""

from .models import (
    ActionRequest,
    ActionResult,
    ControlSnapshot,
    ProjectTreeInventory,
    ProjectTreeNodeSnapshot,
    TreeItemRenameResult,
    WindowSnapshot,
    WindowState,
    action_request_from_payload,
)
from .service import KVStudioWorker

__all__ = [
    "ActionRequest",
    "ActionResult",
    "ControlSnapshot",
    "KVStudioWorker",
    "ProjectTreeInventory",
    "ProjectTreeNodeSnapshot",
    "TreeItemRenameResult",
    "WindowSnapshot",
    "WindowState",
    "action_request_from_payload",
]
