from __future__ import annotations

import pytest

from autocomp.worker.adapter import FakeKVStudioAdapter, PywinautoKVStudioAdapter
from autocomp.worker.models import (
    ActionKind,
    ActionRequest,
    VisualInputOperation,
    action_request_from_payload,
)
from autocomp.worker.service import KVStudioWorker


def test_visual_action_payloads_are_exact_and_typed() -> None:
    snapshot = action_request_from_payload({"action": "visual_snapshot"})
    click = action_request_from_payload(
        {
            "action": "visual_input",
            "checkpoint": "visual_01",
            "operation": "right_click",
            "x": 12,
            "y": 34,
            "apply": True,
        }
    )

    assert snapshot.kind is ActionKind.VISUAL_SNAPSHOT
    assert click.operation is VisualInputOperation.RIGHT_CLICK
    assert (click.x, click.y) == (12, 34)
    with pytest.raises(ValueError, match="missing or unexpected"):
        action_request_from_payload(
            {
                "action": "visual_input",
                "checkpoint": "visual_01",
                "operation": "key_enter",
                "x": 1,
                "apply": True,
            }
        )
    with pytest.raises(ValueError, match="unsupported visual input"):
        action_request_from_payload(
            {
                "action": "visual_input",
                "checkpoint": "visual_01",
                "operation": "key_delete",
                "apply": True,
            }
        )


def test_visual_input_requires_apply_gate_and_named_checkpoint() -> None:
    adapter = FakeKVStudioAdapter()
    request = ActionRequest(
        ActionKind.VISUAL_INPUT,
        checkpoint="visual_01",
        operation=VisualInputOperation.KEY_ENTER,
        apply=True,
    )

    with pytest.raises(ValueError, match="disabled"):
        KVStudioWorker(adapter).execute(request)
    result = KVStudioWorker(adapter, apply_enabled=True).execute(request)

    assert result.performed is True
    assert adapter.visual_input_calls[0]["operation"] is VisualInputOperation.KEY_ENTER


def test_visual_snapshot_is_read_only_and_structured() -> None:
    result = KVStudioWorker(FakeKVStudioAdapter()).execute(
        ActionRequest(ActionKind.VISUAL_SNAPSHOT)
    )

    assert result.performed is False
    assert result.visual_snapshot is not None
    assert result.visual_snapshot.width == 98
    assert result.audit["mode"] == "dry-run"


class _Window:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def click_input(self, *, button: str, coords: tuple[int, int] | None) -> None:
        self.calls.append(("click", button, coords))

    def double_click_input(self, *, button: str, coords: tuple[int, int] | None) -> None:
        self.calls.append(("double", button, coords))

    def wheel_mouse_input(self, *, wheel_dist: int, coords: tuple[int, int] | None) -> None:
        self.calls.append(("wheel", wheel_dist, coords))

    def type_keys(self, key: str, *, set_foreground: bool) -> None:
        self.calls.append(("key", key, set_foreground))


class _Adapter(PywinautoKVStudioAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.window = _Window()

    def _find_project_tree(self) -> tuple[_Window, object]:
        return self.window, object()

    def _restore_and_focus_editor(self, window: object) -> None:
        assert window is self.window

    def _native_window_bounds(
        self, window: object
    ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        assert window is self.window
        return (100, 100, 400, 400), (110, 130, 310, 330)


def test_visual_coordinates_are_client_relative_and_fail_closed_outside() -> None:
    adapter = _Adapter()

    adapter.visual_input(
        VisualInputOperation.CLICK,
        x=10,
        y=20,
        delta=None,
        text="",
    )
    assert adapter.window.calls == [("click", "left", (20, 50))]

    with pytest.raises(ValueError, match="outside"):
        adapter.visual_input(
            VisualInputOperation.RIGHT_CLICK,
            x=200,
            y=0,
            delta=None,
            text="",
        )
    assert len(adapter.window.calls) == 1


@pytest.mark.parametrize(
    ("operation", "key"),
    [
        (VisualInputOperation.KEY_ENTER, "{ENTER}"),
        (VisualInputOperation.KEY_ESCAPE, "{ESC}"),
        (VisualInputOperation.KEY_F2, "{F2}"),
        (VisualInputOperation.KEY_CTRL_A, "^a"),
    ],
)
def test_visual_key_operations_are_fixed_allowlist(
    operation: VisualInputOperation, key: str
) -> None:
    adapter = _Adapter()

    adapter.visual_input(operation, x=None, y=None, delta=None, text="")

    assert adapter.window.calls == [("key", key, False)]
