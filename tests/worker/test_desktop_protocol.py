from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from autocomp.desktop import DesktopFrame, DesktopInputOperation, DesktopWindow
from autocomp.worker.adapter import FakeKVStudioAdapter
from autocomp.worker.models import ActionKind, ActionRequest, action_request_from_payload
from autocomp.worker.service import KVStudioWorker


class DesktopStub:
    def __init__(self) -> None:
        png = b"not-a-real-png"
        self.windows = (DesktopWindow(101, "Calculator", 202, (10, 20, 410, 320), False),)
        self.frame = DesktopFrame(
            handle=101,
            title="Calculator",
            process_id=202,
            bounds=(10, 20, 410, 320),
            width=400,
            height=300,
            png_base64=base64.b64encode(png).decode("ascii"),
            png_sha256="a" * 64,
        )
        self.snapshot_calls: list[dict[str, object]] = []
        self.input_calls: list[dict[str, object]] = []

    def enumerate_windows(self) -> tuple[DesktopWindow, ...]:
        return self.windows

    def snapshot(
        self,
        *,
        handle: int,
        expected_pid: int,
        expected_title: str,
    ) -> DesktopFrame:
        self.snapshot_calls.append(
            {
                "handle": handle,
                "expected_pid": expected_pid,
                "expected_title": expected_title,
            }
        )
        return self.frame

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
    ) -> bool:
        self.input_calls.append(
            {
                "handle": handle,
                "expected_pid": expected_pid,
                "expected_title": expected_title,
                "operation": operation,
                "x": x,
                "y": y,
                "delta": delta,
                "text": text,
            }
        )
        return True


def _desktop_input_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": "desktop_input",
        "window_handle": 101,
        "expected_pid": 202,
        "expected_title": "Calculator",
        "checkpoint": "desktop_01",
        "operation": "click",
        "x": 20,
        "y": 30,
        "apply": True,
    }
    payload.update(updates)
    return payload


def test_desktop_window_and_snapshot_payloads_are_exact_and_typed() -> None:
    windows = action_request_from_payload({"action": "desktop_windows"})
    snapshot = action_request_from_payload(
        {
            "action": "desktop_snapshot",
            "window_handle": 101,
            "expected_pid": 202,
            "expected_title": "Calculator",
        }
    )

    assert windows.kind is ActionKind.DESKTOP_WINDOWS
    assert snapshot.kind is ActionKind.DESKTOP_SNAPSHOT
    assert snapshot.window_handle == 101
    assert snapshot.expected_pid == 202
    assert snapshot.expected_title == "Calculator"
    with pytest.raises(ValueError, match="missing or unexpected"):
        action_request_from_payload(
            {
                "action": "desktop_snapshot",
                "window_handle": 101,
                "expected_pid": 202,
                "expected_title": "Calculator",
                "command": "notepad.exe",
            }
        )


@pytest.mark.parametrize(
    ("operation", "fields"),
    [
        ("click", {"x": 1, "y": 2}),
        ("right", {"x": 1, "y": 2}),
        ("double", {"x": 1, "y": 2}),
        ("wheel", {"x": 1, "y": 2, "delta": -2}),
        ("type_text", {"text": "XRF Assay Station"}),
        ("key_enter", {}),
        ("key_escape", {}),
        ("key_ctrl_a", {}),
        ("key_f2", {}),
        ("tab", {}),
        ("shift_tab", {}),
    ],
)
def test_desktop_input_operation_allowlist(
    operation: str,
    fields: dict[str, object],
) -> None:
    payload = _desktop_input_payload(operation=operation)
    payload.pop("x")
    payload.pop("y")
    payload.update(fields)

    request = action_request_from_payload(payload)

    assert request.desktop_operation is DesktopInputOperation(operation)


def test_desktop_input_rejects_arbitrary_keys_and_unsafe_parameters() -> None:
    with pytest.raises(ValueError, match="unsupported desktop input"):
        action_request_from_payload(_desktop_input_payload(operation="key_delete", x=None, y=None))
    with pytest.raises(ValueError, match="missing or unexpected"):
        action_request_from_payload(_desktop_input_payload(command="whoami"))
    with pytest.raises(ValueError, match="-12 to 12"):
        action_request_from_payload(
            _desktop_input_payload(operation="wheel", delta=120, x=1, y=2)
        )
    with pytest.raises(ValueError, match="unsafe text"):
        action_request_from_payload(
            _desktop_input_payload(operation="type_text", text="line1\nline2", x=None, y=None)
        )


def test_desktop_read_actions_return_structured_adapter_results() -> None:
    desktop = DesktopStub()
    worker = KVStudioWorker(FakeKVStudioAdapter(), desktop_adapter=desktop)

    windows = worker.execute(ActionRequest(ActionKind.DESKTOP_WINDOWS))
    frame = worker.execute(
        ActionRequest(
            ActionKind.DESKTOP_SNAPSHOT,
            window_handle=101,
            expected_pid=202,
            expected_title="Calculator",
        )
    )

    assert worker.desktop_available is True
    assert windows.performed is False
    assert windows.desktop_windows == desktop.windows
    assert frame.desktop_snapshot == desktop.frame
    assert desktop.snapshot_calls == [
        {"handle": 101, "expected_pid": 202, "expected_title": "Calculator"}
    ]


def test_desktop_input_requires_adapter_apply_gate_and_checkpoint() -> None:
    desktop = DesktopStub()
    request = action_request_from_payload(_desktop_input_payload())

    with pytest.raises(ValueError, match="not configured"):
        KVStudioWorker(FakeKVStudioAdapter(), apply_enabled=True).execute(request)
    with pytest.raises(ValueError, match="disabled"):
        KVStudioWorker(FakeKVStudioAdapter(), desktop_adapter=desktop).execute(request)
    invalid_checkpoint = replace(request, checkpoint="bad checkpoint")
    with pytest.raises(ValueError, match="checkpoint"):
        KVStudioWorker(
            FakeKVStudioAdapter(), apply_enabled=True, desktop_adapter=desktop
        ).execute(invalid_checkpoint)


def test_desktop_input_passes_only_pinned_identity_and_allowlisted_operation() -> None:
    desktop = DesktopStub()
    payload = _desktop_input_payload(operation="type_text", text="XRF Assay Station")
    payload.pop("x")
    payload.pop("y")
    request = action_request_from_payload(payload)
    worker = KVStudioWorker(
        FakeKVStudioAdapter(),
        apply_enabled=True,
        desktop_adapter=desktop,
    )

    result = worker.execute(request)

    assert result.performed is True
    assert desktop.snapshot_calls == []
    assert desktop.input_calls == [
        {
            "handle": 101,
            "expected_pid": 202,
            "expected_title": "Calculator",
            "operation": "type_text",
            "x": None,
            "y": None,
            "delta": None,
            "text": "XRF Assay Station",
        }
    ]
