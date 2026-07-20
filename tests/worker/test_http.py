import http.client
import json
import threading

import pytest

from autocomp.worker.adapter import FakeKVStudioAdapter
from autocomp.worker.http import WorkerHttpServer
from autocomp.worker.models import WindowSnapshot
from autocomp.worker.service import KVStudioWorker

TOKEN = "test-secret-that-is-at-least-32-bytes"


@pytest.fixture
def server():
    adapter = FakeKVStudioAdapter((WindowSnapshot("KV STUDIO", 42),))
    instance = WorkerHttpServer(KVStudioWorker(adapter), token=TOKEN)
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
    status, body, _ = request(server, "GET", "/health")

    assert status == 200
    assert json.loads(body) == {"status": "ok"}


def test_inventory_is_the_only_post_action(server) -> None:
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


def test_rejects_other_actions_and_invalid_content_type(server) -> None:
    status, body, _ = request(
        server,
        "POST",
        "/v1/action",
        body=b'{"action":"expand_tree_item"}',
        headers={"Content-Type": "application/json"},
    )
    assert status == 400
    assert json.loads(body)["error"] == "only_inventory_is_supported"

    status, body, _ = request(server, "POST", "/v1/action", body=b"{}")
    assert status == 415
    assert json.loads(body)["error"] == "content_type_must_be_application_json"


def test_non_loopback_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow_remote"):
        WorkerHttpServer(KVStudioWorker(FakeKVStudioAdapter()), token=TOKEN, host="0.0.0.0")


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
