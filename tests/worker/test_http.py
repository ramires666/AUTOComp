import http.client
import json
import threading
from pathlib import Path

import pytest

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
    assert json.loads(body) == {
        "status": "ok",
        "service": "autocomp-worker",
        "api_version": "1",
        "mode": "offline",
    }
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
    assert "rename_tree_item" in payload["actions"]
    assert payload["post_action_audit"] == {"required": True, "configured": True}


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
