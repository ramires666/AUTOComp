"""Safe, offline-only Windows UI automation primitives for KV STUDIO."""

from .models import (
    ActionRequest,
    ActionResult,
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
    action_request_from_payload,
)
from .service import DesktopWorker, KVStudioWorker

__all__ = [
    "ActionRequest",
    "ActionResult",
    "ControlSnapshot",
    "KVStudioWorker",
    "DesktopWorker",
    "MenuItemSnapshot",
    "ProjectTreeInventory",
    "ProjectTreeNodeSnapshot",
    "TreeItemRenameResult",
    "TreeItemMenuInspection",
    "VisualInputOperation",
    "VisualSnapshot",
    "WindowSnapshot",
    "WindowState",
    "action_request_from_payload",
]
