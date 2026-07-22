import http.client
import json
import threading
from pathlib import Path

import pytest

from autocomp.desktop import DesktopClipboardText, DesktopFrame, DesktopWindow
from autocomp.worker.adapter import FakeKVStudioAdapter
from autocomp.worker.http import WorkerHttpServer
from autocomp.worker.models import ActionRequest, ActionResult, WindowSnapshot
from autocomp.worker.service import KVStudioWorker

TOKEN = "test-secret-that-is-at-least-32-bytes"


class CountingWorker(KVStudioWorker):
    def __init__(self, adapter: FakeKVStudioAdapter) -> None:
        super().__init__(adapter)
        self.execute_calls = 0

    def execute(self, request: ActionRequest) -> ActionResult:
        self.execute_calls += 1
        return super().execute(request)


class HttpDesktopStub:
    def __init__(self) -> None:
        self.input_calls = 0

    def enumerate_windows(self) -> tuple[DesktopWindow, ...]:
        return (DesktopWindow(101, "Calculator", 202, (0, 0, 400, 300), False),)

    def snapshot(
        self, *, handle: int, expected_pid: int, expected_title: str
    ) -> DesktopFrame:
        return DesktopFrame(
            handle,
            expected_title,
            expected_pid,
            (0, 0, 400, 300),
            400,
            300,
            "cG5n",
            "a" * 64,
        )

    def clipboard_text(
        self, *, handle: int, expected_pid: int, expected_title: str
    ) -> DesktopClipboardText:
        del handle, expected_pid, expected_title
        return DesktopClipboardText("XRF assay", 9, 9, "b" * 64)

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
        del handle, expected_pid, expected_title, operation, x, y, delta, text
        self.input_calls += 1
        return True


@pytest.fixture
def server(tmp_path: Path):
    adapter = FakeKVStudioAdapter((WindowSnapshot("KV STUDIO", 42),))
    instance = WorkerHttpServer(
        KVStudioWorker(adapter),
        token=TOKEN,
        audit_log_path=tmp_path / "worker-audit.jsonl",
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def request(server, method, path, *, token=TOKEN, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address)
    all_headers = {"Authorization": f"Bearer {token}"}
    all_headers.update(headers or {})
    connection.request(method, path, body=body, headers=all_headers)
    response = connection.getresponse()
    received = response.read()
    connection.close()
    return response.status, received, dict(response.getheaders())


def test_health_requires_bearer_token(server) -> None:
    status, _, headers = request(server, "GET", "/health", token="wrong")

    assert status == 401
    assert headers["WWW-Authenticate"] == "Bearer"


def test_non_ascii_bearer_is_rejected_without_crashing_server(server) -> None:
    status, _, _ = request(server, "GET", "/health", token="é" * 32)
    healthy_status, _, _ = request(server, "GET", "/health")

    assert status == 401
    assert healthy_status == 200


def test_health_is_authenticated(server) -> None:
    status, body, headers = request(server, "GET", "/health")

    assert status == 200
    payload = json.loads(body)
    assert {key: payload[key] for key in ("status", "service", "api_version", "mode")} == {
        "status": "ok",
        "service": "autocomp-worker",
        "api_version": "1",
        "mode": "offline",
    }
    assert payload["build_id"]
    assert len(payload["boot_id"]) == 32
    assert payload["started_at"].endswith("+00:00")
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_capabilities_explicitly_exclude_shell_input_and_plc(server) -> None:
    status, body, _ = request(server, "GET", "/v1/capabilities")

    assert status == 200
    payload = json.loads(body)
    assert payload["authentication"] == "bearer"
    assert payload["arbitrary_shell"] is False
    assert payload["arbitrary_input"] is False
    assert payload["plc_operations"] is False
    assert "inventory" in payload["actions"]
    assert "activate_tree_item" in payload["actions"]
    assert "activate_tree_item" in payload["mutating_actions"]
    assert "rename_tree_item" in payload["actions"]
    assert "desktop_input" not in payload["actions"]
    assert payload["constrained_desktop_input"] is False
    assert payload["desktop_input_operations"] == []
    assert payload["post_action_audit"] == {"required": True, "configured": True}
    assert payload["build_id"] == server.build_id
    assert payload["boot_id"] == server.boot_id
    assert payload["started_at"] == server.started_at
    assert payload["operation_limits"] == {
        "request_body_bytes": 16 * 1024,
        "request_timeout_seconds": 15.0,
        "desktop_sequence_operations": 8,
        "desktop_text_characters": 512,
        "desktop_pause_milliseconds": 1000,
        "desktop_wheel_delta": 12,
        "desktop_frame_pixels": 50_000_000,
        "desktop_png_bytes": 64 * 1024 * 1024,
        "enumerated_owned_windows": 64,
        "desktop_clipboard_utf8_bytes": 8 * 1024 * 1024,
    }


def test_status_is_authenticated_and_does_not_require_an_audit_log() -> None:
    instance = WorkerHttpServer(
        KVStudioWorker(FakeKVStudioAdapter((WindowSnapshot("KV STUDIO", 42),))),
        token=TOKEN,
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        status, body, _ = request(instance, "GET", "/v1/status")
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()

    assert status == 200
    assert json.loads(body)["worker"]["performed"] is False


def test_http_exposes_desktop_actions_only_when_adapter_is_wired(tmp_path: Path) -> None:
    desktop = HttpDesktopStub()
    instance = WorkerHttpServer(
        KVStudioWorker(
            FakeKVStudioAdapter(),
            apply_enabled=True,
            desktop_adapter=desktop,
        ),
        token=TOKEN,
        audit_log_path=tmp_path / "worker-audit.jsonl",
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        capability_status, capability_body, _ = request(
            instance, "GET", "/v1/capabilities"
        )
        windows_status, windows_body, _ = request(
            instance,
            "POST",
            "/v1/action",
            body=b'{"action":"desktop_windows"}',
            headers={"Content-Type": "application/json"},
        )
        clipboard_status, clipboard_body, _ = request(
            instance,
            "POST",
            "/v1/action",
            body=json.dumps(
                {
                    "action": "desktop_clipboard_text",
                    "window_handle": 101,
                    "expected_pid": 202,
                    "expected_title": "Calculator",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        secret_text = "temporary-secret-not-for-audit"
        input_body = json.dumps(
            {
                "action": "desktop_input",
                "window_handle": 101,
                "expected_pid": 202,
                "expected_title": "Calculator",
                "checkpoint": "desktop_01",
                "operation": "type_text",
                "text": secret_text,
                "apply": True,
            }
        ).encode()
        input_status, input_response, _ = request(
            instance,
            "POST",
            "/v1/action",
            body=input_body,
            headers={"Content-Type": "application/json"},
        )
        sequence_secret = "sequence-secret-not-for-audit"
        sequence_body = json.dumps(
            {
                "action": "desktop_input_sequence",
                "window_handle": 101,
                "expected_pid": 202,
                "expected_title": "Calculator",
                "checkpoint": "desktop_sequence_01",
                "operations": [
                    {"operation": "type_text", "text": sequence_secret, "pause_ms": 0},
                    {"operation": "key_enter", "pause_ms": 0},
                ],
                "apply": True,
            }
        ).encode()
        sequence_status, sequence_response, _ = request(
            instance,
            "POST",
            "/v1/action",
            body=sequence_body,
            headers={"Content-Type": "application/json"},
        )
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()

    capabilities = json.loads(capability_body)
    assert capability_status == 200
    assert capabilities["constrained_desktop_input"] is True
    assert {
        "key_ctrl_c",
        "key_ctrl_d",
        "key_ctrl_home",
        "key_ctrl_shift_end",
    }.issubset(capabilities["desktop_input_operations"])
    assert "desktop_windows" in capabilities["actions"]
    assert "desktop_snapshot" in capabilities["actions"]
    assert "desktop_clipboard_text" in capabilities["actions"]
    assert "desktop_input" in capabilities["mutating_actions"]
    assert "desktop_input_sequence" in capabilities["mutating_actions"]
    assert windows_status == 200
    assert json.loads(windows_body)["desktop_windows"][0]["handle"] == 101
    assert clipboard_status == 200
    assert json.loads(clipboard_body)["desktop_clipboard_text"]["text"] == "XRF assay"
    assert input_status == 200
    assert json.loads(input_response)["performed"] is True
    assert sequence_status == 200
    assert json.loads(sequence_response)["performed"] is True
    assert desktop.input_calls == 3
    audit_text = instance.audit_log_path.read_text(encoding="utf-8")
    assert secret_text not in audit_text
    assert sequence_secret not in audit_text
    assert f'"text_length":{len(secret_text)}' in audit_text
    records = [json.loads(line) for line in audit_text.splitlines()]
    sequence_records = [
        record for record in records if record["action"] == "desktop_input_sequence"
    ]
    assert [record["phase"] for record in sequence_records] == ["intent", "outcome"]
    assert sequence_records[0]["operation_count"] == 2
    assert sequence_records[0]["operations"][0]["text_length"] == len(sequence_secret)


def test_desktop_only_worker_exposes_no_application_specific_actions(
    tmp_path: Path,
) -> None:
    instance = WorkerHttpServer(
        KVStudioWorker(None, apply_enabled=True, desktop_adapter=HttpDesktopStub()),
        token=TOKEN,
        audit_log_path=tmp_path / "worker-audit.jsonl",
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        status, body, _ = request(instance, "GET", "/v1/capabilities")
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()

    assert status == 200
    actions = set(json.loads(body)["actions"])
    assert actions == {
        "desktop_windows",
        "desktop_snapshot",
        "desktop_clipboard_text",
        "desktop_input",
        "desktop_input_sequence",
    }


def test_inventory_post_is_executed_and_durably_audited(server) -> None:
    status, body, _ = request(
        server,
        "POST",
        "/v1/action",
        body=b'{"action":"inventory"}',
        headers={"Content-Type": "application/json"},
    )

    assert status == 200
    payload = json.loads(body)
    assert payload["performed"] is False
    assert payload["windows"][0]["title"] == "KV STUDIO"

    records = [json.loads(line) for line in server.audit_log_path.read_text().splitlines()]
    assert [record["phase"] for record in records] == ["intent", "outcome"]
    assert records[0]["action"] == "inventory"
    assert records[1]["performed"] is False
    assert records[0]["request_id"] == records[1]["request_id"]
    assert payload["request_id"] == records[0]["request_id"]
    assert TOKEN not in server.audit_log_path.read_text()
    assert "Authorization" not in server.audit_log_path.read_text()


def test_generic_post_accepts_typed_rename_dry_run(server) -> None:
    body = json.dumps(
        {
            "action": "rename_tree_item",
            "checkpoint": "",
            "locator": [0, 2],
            "expected_path": ["项目", "测金位"],
            "expected_source": "测金位",
            "target": "XRF Assay Station",
            "apply": False,
        }
    ).encode()

    status, response_body, _ = request(
        server,
        "POST",
        "/v1/action",
        body=body,
        headers={"Content-Type": "application/json"},
    )

    assert status == 200
    payload = json.loads(response_body)
    assert payload["action"] == "rename_tree_item"
    assert payload["performed"] is False
    assert payload["before"] == "测金位"
    assert payload["after"] == "测金位"


def test_generic_post_rejects_extra_fields_that_could_encode_shell_input(server) -> None:
    status, body, _ = request(
        server,
        "POST",
        "/v1/action",
        body=b'{"action":"status","command":"whoami"}',
        headers={"Content-Type": "application/json"},
    )

    assert status == 400
    assert json.loads(body)["error"] == "invalid_action_request"


def test_rejects_other_actions_and_invalid_content_type(server) -> None:
    status, body, _ = request(
        server,
        "POST",
        "/v1/action",
        body=b'{"action":"expand_tree_item"}',
        headers={"Content-Type": "application/json"},
    )
    assert status == 400
    assert json.loads(body)["error"] == "invalid_action_request"

    status, body, _ = request(server, "POST", "/v1/action", body=b"{}")
    assert status == 415
    assert json.loads(body)["error"] == "content_type_must_be_application_json"


def test_non_loopback_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow_remote"):
        WorkerHttpServer(KVStudioWorker(FakeKVStudioAdapter()), token=TOKEN, host="0.0.0.0")


def test_non_loopback_bind_accepts_explicit_opt_in_and_strong_token(tmp_path: Path) -> None:
    instance = WorkerHttpServer(
        KVStudioWorker(FakeKVStudioAdapter()),
        token=TOKEN,
        host="0.0.0.0",
        allow_remote=True,
        audit_log_path=tmp_path / "worker-audit.jsonl",
    )
    instance.server_close()


def test_non_loopback_bind_still_requires_token() -> None:
    with pytest.raises(ValueError, match="Bearer token"):
        WorkerHttpServer(
            KVStudioWorker(FakeKVStudioAdapter()),
            token="",
            host="0.0.0.0",
            allow_remote=True,
        )


def test_post_is_rejected_before_execution_without_audit_log() -> None:
    adapter = FakeKVStudioAdapter((WindowSnapshot("KV STUDIO", 42),))
    instance = WorkerHttpServer(KVStudioWorker(adapter), token=TOKEN)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        status, body, _ = request(
            instance,
            "POST",
            "/v1/action",
            body=b'{"action":"inventory"}',
            headers={"Content-Type": "application/json"},
        )
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()

    assert status == 503
    assert json.loads(body)["error"] == "audit_not_configured"


def test_post_is_rejected_before_execution_when_intent_cannot_be_written(
    tmp_path: Path,
) -> None:
    worker = CountingWorker(FakeKVStudioAdapter())
    instance = WorkerHttpServer(
        worker,
        token=TOKEN,
        audit_log_path=tmp_path,
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        status, body, _ = request(
            instance,
            "POST",
            "/v1/action",
            body=b'{"action":"inventory"}',
            headers={"Content-Type": "application/json"},
        )
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()

    assert status == 503
    assert json.loads(body)["error"] == "audit_unavailable"
    assert worker.execute_calls == 0


def test_rejects_short_token() -> None:
    with pytest.raises(ValueError, match="32-512"):
        WorkerHttpServer(KVStudioWorker(FakeKVStudioAdapter()), token="too-short")


@pytest.mark.parametrize(
    "token",
    [" " * 32, "a" * 31, "a" * 513, "a" * 31 + "\n", "я" * 32],
)
def test_rejects_unsafe_tokens(token) -> None:
    with pytest.raises(ValueError, match="printable non-whitespace"):
        WorkerHttpServer(KVStudioWorker(FakeKVStudioAdapter()), token=token)
