from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autocomp.cli import main
from autocomp.translation.client import (
    _BATCH_RESPONSE_FORMAT,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from autocomp.translation.models import ProviderBatchItem


class _ModelHandler(BaseHTTPRequestHandler):
    calls = 0
    models_calls = 0
    authorization = ""
    model_id = "fake-local-model"
    post_models: list[str] = []
    response_formats: list[bool] = []
    reject_json_mode = False

    def do_GET(self) -> None:  # noqa: N802
        type(self).models_calls += 1
        assert self.path == "/v1/models"
        type(self).authorization = self.headers.get("Authorization", "")
        body = json.dumps({"object": "list", "data": [{"id": type(self).model_id}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        type(self).calls += 1
        type(self).authorization = self.headers.get("Authorization", "")
        assert self.path == "/v1/chat/completions"
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        type(self).post_models.append(request["model"])
        type(self).response_formats.append("response_format" in request)
        if request["model"] != type(self).model_id:
            self.send_error(404, "model not found")
            return
        if type(self).reject_json_mode and "response_format" in request:
            self.send_error(400, "response_format unsupported")
            return
        user_content = json.loads(request["messages"][1]["content"])
        items = [
            {
                "record_id": item["record_id"],
                "translation": item["text"].replace("启动", "Start").replace("停止", "Stop"),
                "notes": "",
                "confidence": 0.99,
            }
            for item in user_content["items"]
        ]
        content = json.dumps({"items": items}, ensure_ascii=False)
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_server_schema_matches_client_batch_schema() -> None:
    schema_path = Path(__file__).parents[2] / "schemas" / "autocomp-translation-batch.schema.json"

    assert (
        json.loads(schema_path.read_text(encoding="utf-8"))
        == _BATCH_RESPONSE_FORMAT["json_schema"]["schema"]
    )


def test_cli_translation_uses_one_strict_batch_request(tmp_path) -> None:
    _ModelHandler.calls = 0
    _ModelHandler.models_calls = 0
    _ModelHandler.authorization = ""
    _ModelHandler.model_id = "fake-local-model"
    _ModelHandler.post_models = []
    _ModelHandler.response_formats = []
    _ModelHandler.reject_json_mode = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = tmp_path / "config.json"
        config.write_text(
            json.dumps(
                {
                    "llm": {
                        "endpoint": f"http://127.0.0.1:{server.server_port}/v1",
                        "model": "auto",
                    },
                    "safety": {"batch_size": 25},
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / ".env").write_text(
            "AUTOCOMP_LLM_API_KEY=dotenv-test-key\n",
            encoding="utf-8",
        )
        inventory = tmp_path / "inventory.json"
        inventory.write_text(
            json.dumps(
                [
                    {
                        "record_id": "one",
                        "source_text": "启动 X0",
                        "kind": "comment",
                    },
                    {
                        "record_id": "two",
                        "source_text": "停止 DM100",
                        "kind": "comment",
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        output = tmp_path / "manifest.json"

        exit_code = main(
            [
                "translate",
                str(inventory),
                "--config",
                str(config),
                "--checkpoint",
                "01-test",
                "--output",
                str(output),
            ]
        )

        manifest = json.loads(output.read_text(encoding="utf-8"))
        assert exit_code == 0
        assert _ModelHandler.calls == 1
        assert _ModelHandler.models_calls == 1
        assert _ModelHandler.post_models == ["fake-local-model"]
        assert _ModelHandler.response_formats == [True]
        assert _ModelHandler.authorization == "Bearer dotenv-test-key"
        assert [item["target_text"] for item in manifest["decisions"]] == [
            "Start X0",
            "Stop DM100",
        ]
        assert all(item["provider"] == "batch_provider" for item in manifest["decisions"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_provider_rediscovers_model_after_server_switch() -> None:
    _ModelHandler.calls = 0
    _ModelHandler.models_calls = 0
    _ModelHandler.model_id = "model-a"
    _ModelHandler.post_models = []
    _ModelHandler.response_formats = []
    _ModelHandler.reject_json_mode = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="auto",
            )
        )
        items = [ProviderBatchItem("one", "启动", "")]

        provider.translate_batch(items, glossary={})
        _ModelHandler.model_id = "model-b"
        provider.translate_batch(items, glossary={})

        assert _ModelHandler.models_calls == 2
        assert _ModelHandler.post_models == ["model-a", "model-a", "model-b"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_json_mode_falls_back_for_compatible_server() -> None:
    _ModelHandler.calls = 0
    _ModelHandler.model_id = "fixed-model"
    _ModelHandler.post_models = []
    _ModelHandler.response_formats = []
    _ModelHandler.reject_json_mode = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model="fixed-model",
            )
        )

        result = provider.translate("启动", context="", glossary={})

        assert result.translation == "Start"
        assert _ModelHandler.response_formats == [True, False]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
