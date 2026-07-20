"""Structured, serialisable values exchanged with the UI worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ActionKind(StrEnum):
    """The deliberately small set of operations understood by this worker."""

    INVENTORY = "inventory"
    EXPAND_TREE_ITEM = "expand_tree_item"


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    """A non-live snapshot of an accessible UI control."""

    name: str
    control_type: str
    automation_id: str = ""
    class_name: str = ""
    framework_id: str = ""
    native_handle: int = 0
    rectangle: tuple[int, int, int, int] | None = None
    enabled: bool = False
    visible: bool = False
    truncated: bool = False
    children: tuple[ControlSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    """A non-live snapshot of an allowlisted KV STUDIO window."""

    title: str
    process_id: int
    controls: tuple[ControlSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectTreeNodeSnapshot:
    """One project-tree node captured with its full logical hierarchy."""

    name: str
    path: tuple[str, ...]
    depth: int
    sibling_index: int
    locator: tuple[int, ...]
    initial_expansion_state: str = "unknown"
    expanded_for_inventory: bool = False
    visible: bool = False
    truncated: bool = False
    children: tuple[ProjectTreeNodeSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectTreeInventory:
    """Bounded, reversible inventory of KV STUDIO's native project tree."""

    window_title: str
    process_id: int
    automation_id: str
    item_count: int
    expanded_count: int
    restored_count: int
    restore_requested: bool
    complete: bool = True
    restoration_complete: bool = True
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    roots: tuple[ProjectTreeNodeSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """A request that cannot encode shell commands, keys, or arbitrary clicks."""

    kind: ActionKind
    checkpoint: str = ""
    target_path: tuple[str, ...] = ()
    apply: bool = False


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome suitable for a structured audit log."""

    kind: ActionKind
    performed: bool
    message: str
    windows: tuple[WindowSnapshot, ...] = ()
    audit: dict[str, str] = field(default_factory=dict)
