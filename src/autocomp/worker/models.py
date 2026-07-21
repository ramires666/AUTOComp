"""Structured, serialisable values exchanged with the UI worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from autocomp.desktop import DesktopFrame, DesktopInputOperation, DesktopWindow


class ActionKind(StrEnum):
    """The deliberately small set of operations understood by this worker."""

    INVENTORY = "inventory"
    EXPAND_TREE_ITEM = "expand_tree_item"
    STATUS = "status"
    INVENTORY_PROJECT_TREE = "inventory_project_tree"
    ACTIVATE_TREE_ITEM = "activate_tree_item"
    RENAME_TREE_ITEM = "rename_tree_item"
    PROBE_TREE_ITEM_RENAME = "probe_tree_item_rename"
    INSPECT_TREE_ITEM_MENU = "inspect_tree_item_menu"
    VISUAL_SNAPSHOT = "visual_snapshot"
    VISUAL_INPUT = "visual_input"
    DESKTOP_WINDOWS = "desktop_windows"
    DESKTOP_SNAPSHOT = "desktop_snapshot"
    DESKTOP_INPUT = "desktop_input"
    DESKTOP_INPUT_SEQUENCE = "desktop_input_sequence"


class VisualInputOperation(StrEnum):
    CLICK = "click"
    RIGHT_CLICK = "right_click"
    DOUBLE_CLICK = "double_click"
    WHEEL = "wheel"
    TYPE_TEXT = "type_text"
    KEY_ENTER = "key_enter"
    KEY_ESCAPE = "key_escape"
    KEY_F2 = "key_f2"
    KEY_CTRL_A = "key_ctrl_a"


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
class MenuItemSnapshot:
    """One visible item from the exact tree node's transient context menu."""

    text: str
    automation_id: str = ""
    class_name: str = ""
    control_type: str = "MenuItem"
    native_handle: int = 0
    runtime_id: tuple[int, ...] = ()
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class TreeItemMenuInspection:
    """Bounded snapshot of a context menu opened for one pinned tree item."""

    window_title: str
    process_id: int
    locator: tuple[int, ...]
    path: tuple[str, ...]
    source: str
    menu_native_handle: int = 0
    menu_automation_id: str = ""
    menu_class_name: str = ""
    items: tuple[MenuItemSnapshot, ...] = ()
    complete: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisualSnapshot:
    """PNG of the unique KV editor client area and its screen bounds."""

    png_base64: str
    window_bounds: tuple[int, int, int, int]
    client_bounds: tuple[int, int, int, int]
    width: int
    height: int
    process_id: int
    window_title: str


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
class DesktopInputStep:
    """One strictly bounded operation in an atomic worker request."""

    operation: DesktopInputOperation
    x: int | None = None
    y: int | None = None
    delta: int | None = None
    text: str = ""
    pause_ms: int = 120


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
    operation: VisualInputOperation | None = None
    x: int | None = None
    y: int | None = None
    delta: int | None = None
    text: str = ""
    window_handle: int = 0
    expected_pid: int = 0
    expected_title: str = ""
    desktop_operation: DesktopInputOperation | None = None
    desktop_operations: tuple[DesktopInputStep, ...] = ()


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
    tree_item_menu_inspection: TreeItemMenuInspection | None = None
    visual_snapshot: VisualSnapshot | None = None
    desktop_windows: tuple[DesktopWindow, ...] = ()
    desktop_snapshot: DesktopFrame | None = None


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
        ActionKind.ACTIVATE_TREE_ITEM: (
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "apply",
            },
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "apply",
            },
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
        ActionKind.INSPECT_TREE_ITEM_MENU: (
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "apply",
            },
            {
                "action",
                "checkpoint",
                "locator",
                "expected_path",
                "expected_source",
                "apply",
            },
        ),
        ActionKind.VISUAL_SNAPSHOT: ({"action"}, {"action"}),
        ActionKind.VISUAL_INPUT: (
            {"action", "checkpoint", "operation", "x", "y", "delta", "text", "apply"},
            {"action", "checkpoint", "operation", "apply"},
        ),
        ActionKind.DESKTOP_WINDOWS: ({"action"}, {"action"}),
        ActionKind.DESKTOP_SNAPSHOT: (
            {"action", "window_handle", "expected_pid", "expected_title"},
            {"action", "window_handle", "expected_pid", "expected_title"},
        ),
        ActionKind.DESKTOP_INPUT: (
            {
                "action",
                "window_handle",
                "expected_pid",
                "expected_title",
                "checkpoint",
                "operation",
                "x",
                "y",
                "delta",
                "text",
                "apply",
            },
            {
                "action",
                "window_handle",
                "expected_pid",
                "expected_title",
                "checkpoint",
                "operation",
                "apply",
            },
        ),
        ActionKind.DESKTOP_INPUT_SEQUENCE: (
            {
                "action",
                "window_handle",
                "expected_pid",
                "expected_title",
                "checkpoint",
                "operations",
                "apply",
            },
            {
                "action",
                "window_handle",
                "expected_pid",
                "expected_title",
                "checkpoint",
                "operations",
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
        if kind
        in {
            ActionKind.ACTIVATE_TREE_ITEM,
            ActionKind.RENAME_TREE_ITEM,
            ActionKind.PROBE_TREE_ITEM_RENAME,
            ActionKind.INSPECT_TREE_ITEM_MENU,
        }
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
    operation: VisualInputOperation | None = None
    x: int | None = None
    y: int | None = None
    delta: int | None = None
    text = ""
    window_handle = 0
    expected_pid = 0
    expected_title = ""
    desktop_operation: DesktopInputOperation | None = None
    desktop_operations: tuple[DesktopInputStep, ...] = ()
    if kind is ActionKind.VISUAL_INPUT:
        try:
            operation = VisualInputOperation(payload.get("operation"))
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported visual input operation") from exc
        coordinate_operations = {
            VisualInputOperation.CLICK,
            VisualInputOperation.RIGHT_CLICK,
            VisualInputOperation.DOUBLE_CLICK,
            VisualInputOperation.WHEEL,
        }
        required_fields = {"action", "checkpoint", "operation", "apply"}
        if operation in coordinate_operations:
            required_fields |= {"x", "y"}
            x = _payload_integer(payload.get("x"), "x", minimum=0, maximum=100_000)
            y = _payload_integer(payload.get("y"), "y", minimum=0, maximum=100_000)
        if operation is VisualInputOperation.WHEEL:
            required_fields.add("delta")
            delta = _payload_integer(payload.get("delta"), "delta", minimum=-12_000, maximum=12_000)
            if delta == 0:
                raise ValueError("wheel delta must not be zero")
        if operation is VisualInputOperation.TYPE_TEXT:
            required_fields.add("text")
            text = _payload_text(payload.get("text"), "text", maximum=4096)
            if not text:
                raise ValueError("text must not be empty")
        if set(payload) != required_fields:
            raise ValueError("visual input payload has missing or unexpected fields")
    if kind in {
        ActionKind.DESKTOP_SNAPSHOT,
        ActionKind.DESKTOP_INPUT,
        ActionKind.DESKTOP_INPUT_SEQUENCE,
    }:
        window_handle = _payload_integer(
            payload.get("window_handle"),
            "window_handle",
            minimum=1,
            maximum=2**63 - 1,
        )
        expected_pid = _payload_integer(
            payload.get("expected_pid"),
            "expected_pid",
            minimum=1,
            maximum=2**31 - 1,
        )
        expected_title = _payload_text(
            payload.get("expected_title"), "expected_title", maximum=512
        )
        if not expected_title:
            raise ValueError("expected_title must not be empty")
    if kind is ActionKind.DESKTOP_INPUT:
        try:
            desktop_operation = DesktopInputOperation(payload.get("operation"))
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported desktop input operation") from exc
        coordinate_operations = {
            DesktopInputOperation.CLICK,
            DesktopInputOperation.RIGHT,
            DesktopInputOperation.DOUBLE,
            DesktopInputOperation.WHEEL,
        }
        required_fields = {
            "action",
            "window_handle",
            "expected_pid",
            "expected_title",
            "checkpoint",
            "operation",
            "apply",
        }
        if desktop_operation in coordinate_operations:
            required_fields |= {"x", "y"}
            x = _payload_integer(payload.get("x"), "x", minimum=0, maximum=100_000)
            y = _payload_integer(payload.get("y"), "y", minimum=0, maximum=100_000)
        if desktop_operation is DesktopInputOperation.WHEEL:
            required_fields.add("delta")
            delta = _payload_integer(payload.get("delta"), "delta", minimum=-12, maximum=12)
            if delta == 0:
                raise ValueError("wheel delta must not be zero")
        if desktop_operation is DesktopInputOperation.TYPE_TEXT:
            required_fields.add("text")
            text = _payload_text(payload.get("text"), "text", maximum=512)
            if not text:
                raise ValueError("text must not be empty")
        if set(payload) != required_fields:
            raise ValueError("desktop input payload has missing or unexpected fields")
    if kind is ActionKind.DESKTOP_INPUT_SEQUENCE:
        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list) or not 1 <= len(raw_operations) <= 8:
            raise ValueError("operations must be an array containing 1 to 8 items")
        desktop_operations = tuple(
            _desktop_input_step_from_payload(item, index=index)
            for index, item in enumerate(raw_operations)
        )
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
        operation=operation,
        x=x,
        y=y,
        delta=delta,
        text=text,
        window_handle=window_handle,
        expected_pid=expected_pid,
        expected_title=expected_title,
        desktop_operation=desktop_operation,
        desktop_operations=desktop_operations,
    )


def _desktop_input_step_from_payload(payload: object, *, index: int) -> DesktopInputStep:
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise ValueError(f"operations[{index}] must be an object with string keys")
    try:
        operation = DesktopInputOperation(payload.get("operation"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"operations[{index}] has an unsupported desktop input operation") from exc

    required_fields = {"operation"}
    if "pause_ms" in payload:
        required_fields.add("pause_ms")
        pause_ms = _payload_integer(
            payload.get("pause_ms"),
            f"operations[{index}].pause_ms",
            minimum=0,
            maximum=1000,
        )
    else:
        pause_ms = 120
    x: int | None = None
    y: int | None = None
    delta: int | None = None
    text = ""
    coordinate_operations = {
        DesktopInputOperation.CLICK,
        DesktopInputOperation.RIGHT,
        DesktopInputOperation.DOUBLE,
        DesktopInputOperation.WHEEL,
    }
    if operation in coordinate_operations:
        required_fields |= {"x", "y"}
        x = _payload_integer(
            payload.get("x"), f"operations[{index}].x", minimum=0, maximum=100_000
        )
        y = _payload_integer(
            payload.get("y"), f"operations[{index}].y", minimum=0, maximum=100_000
        )
    if operation is DesktopInputOperation.WHEEL:
        required_fields.add("delta")
        delta = _payload_integer(
            payload.get("delta"),
            f"operations[{index}].delta",
            minimum=-12,
            maximum=12,
        )
        if delta == 0:
            raise ValueError(f"operations[{index}] wheel delta must not be zero")
    if operation is DesktopInputOperation.TYPE_TEXT:
        required_fields.add("text")
        text = _payload_text(payload.get("text"), f"operations[{index}].text", maximum=512)
        if not text:
            raise ValueError(f"operations[{index}] text must not be empty")
    if set(payload) != required_fields:
        raise ValueError(f"operations[{index}] has missing or unexpected fields")
    return DesktopInputStep(
        operation=operation,
        x=x,
        y=y,
        delta=delta,
        text=text,
        pause_ms=pause_ms,
    )


def _payload_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _payload_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise ValueError(f"{field_name} must be an integer from {minimum} to {maximum}")
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
