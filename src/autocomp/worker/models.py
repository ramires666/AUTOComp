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
