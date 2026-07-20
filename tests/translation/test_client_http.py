from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from autocomp.cli import main


class _ModelHandler(BaseHTTPRequestHandler):
    calls = 0
    authorization = ""

    def do_POST(self) -> None:  # noqa: N802
        type(self).calls += 1
        type(self).authorization = self.headers.get("Authorization", "")
        assert self.path == "/v1/chat/completions"
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
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


def test_cli_translation_uses_one_strict_batch_request(tmp_path) -> None:
    _ModelHandler.calls = 0
    _ModelHandler.authorization = ""
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
                        "model": "fake-local-model",
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
