"""Structured, serialisable values exchanged with the UI worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ActionKind(StrEnum):
    """The deliberately small set of operations understood by this worker."""

    INVENTORY = "inventory"
    EXPAND_TREE_ITEM = "expand_tree_item"
    STATUS = "status"
    INVENTORY_PROJECT_TREE = "inventory_project_tree"
    RENAME_TREE_ITEM = "rename_tree_item"
    PROBE_TREE_ITEM_RENAME = "probe_tree_item_rename"


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
class WindowState:
    """Identity and non-mutating state of the single allowlisted editor window."""

    title: str
    process_id: int
    minimized: bool
    enabled: bool
    visible: bool
    project_tree_available: bool


@dataclass(frozen=True, slots=True)
class TreeItemRenameResult:
    """Transactional result returned by the Windows-only tree rename primitive."""

    performed: bool
    before: str
    after: str
    rollback_attempted: bool = False
    rollback_succeeded: bool = False
    error: str = ""


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
    locator: tuple[int, ...] = ()
    expected_path: tuple[str, ...] = ()
    expected_source: str = ""
    target: str = ""
    apply: bool = False
    expand_all: bool = False
    restore_state: bool = True


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome suitable for a structured audit log."""

    kind: ActionKind
    performed: bool
    message: str
    windows: tuple[WindowSnapshot, ...] = ()
    project_tree_inventory: ProjectTreeInventory | None = None
    audit: dict[str, str] = field(default_factory=dict)
    before: str = ""
    after: str = ""
    rollback_attempted: bool = False
    rollback_succeeded: bool = False
    window_state: WindowState | None = None


def action_request_from_payload(payload: object) -> ActionRequest:
    """Parse the exact allowlisted JSON shape for one worker action."""

    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise ValueError("action payload must be a JSON object with string keys")
    raw_kind = payload.get("action")
    if not isinstance(raw_kind, str):
        raise ValueError("action must be a string")
    try:
        kind = ActionKind(raw_kind)
    except ValueError as exc:
        raise ValueError("unsupported action") from exc

    schemas: dict[ActionKind, tuple[set[str], set[str]]] = {
        ActionKind.INVENTORY: ({"action"}, {"action"}),
        ActionKind.STATUS: ({"action"}, {"action"}),
        ActionKind.EXPAND_TREE_ITEM: (
            {"action", "checkpoint", "target_path", "apply"},
            {"action", "checkpoint", "target_path", "apply"},
        ),
        ActionKind.INVENTORY_PROJECT_TREE: (
            {"action", "checkpoint", "expand_all", "restore_state", "apply"},
            {"action", "expand_all", "restore_state", "apply"},
        ),
        ActionKind.RENAME_TREE_ITEM: (
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "target",
                "apply",
            },
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "target",
                "apply",
            },
        ),
        ActionKind.PROBE_TREE_ITEM_RENAME: (
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "target",
                "apply",
            },
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "target",
                "apply",
            },
        ),
    }
    allowed, required = schemas[kind]
    keys = set(payload)
    if keys - allowed or required - keys:
        raise ValueError("action payload has missing or unexpected fields")

    checkpoint = _payload_text(payload.get("checkpoint", ""), "checkpoint", maximum=128)
    target_path = _payload_text_tuple(payload.get("target_path", []), "target_path")
    locator = (
        _payload_locator(payload.get("locator"))
        if kind in {ActionKind.RENAME_TREE_ITEM, ActionKind.PROBE_TREE_ITEM_RENAME}
        else ()
    )
    expected_path = _payload_text_tuple(payload.get("expected_path", []), "expected_path")
    expected_source = _payload_text(
        payload.get("expected_source", ""), "expected_source", maximum=512
    )
    target = _payload_text(payload.get("target", ""), "target", maximum=512)
    apply = _payload_bool(payload.get("apply", False), "apply")
    expand_all = _payload_bool(payload.get("expand_all", False), "expand_all")
    restore_state = _payload_bool(payload.get("restore_state", True), "restore_state")
    return ActionRequest(
        kind=kind,
        checkpoint=checkpoint,
        target_path=target_path,
        locator=locator,
        expected_path=expected_path,
        expected_source=expected_source,
        target=target,
        apply=apply,
        expand_all=expand_all,
        restore_state=restore_state,
    )


def _payload_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _payload_text(value: object, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if len(value) > maximum or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(f"{field_name} contains unsafe text")
    return value


def _payload_text_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(f"{field_name} must be a bounded string array")
    return tuple(_payload_text(part, field_name, maximum=512) for part in value)


def _payload_locator(value: object) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 64
        or any(not isinstance(part, int) or isinstance(part, bool) or part < 0 for part in value)
    ):
        raise ValueError("locator must be a non-empty bounded array of non-negative integers")
    return tuple(value)
