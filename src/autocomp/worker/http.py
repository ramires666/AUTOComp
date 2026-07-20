"""Authenticated, loopback-only HTTP facade for read-only UI inventory."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from .models import ActionKind, ActionRequest
from .service import KVStudioWorker

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_MAX_BODY_BYTES: Final = 16 * 1024
TOKEN_ENVIRONMENT_VARIABLE: Final = "AUTOCOMP_WORKER_TOKEN"


class WorkerHttpServer(ThreadingHTTPServer):
    """A server restricted to authenticated, local inventory requests."""

    daemon_threads = True

    def __init__(
        self,
        worker: KVStudioWorker,
        token: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = 0,
        *,
        allow_remote: bool = False,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        _validate_bind_host(host, allow_remote=allow_remote)
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        resolved_token = token if token is not None else os.getenv(TOKEN_ENVIRONMENT_VARIABLE)
        if not resolved_token:
            raise ValueError(
                f"Bearer token must be supplied or set in {TOKEN_ENVIRONMENT_VARIABLE}"
            )
        if len(resolved_token) < 32:
            raise ValueError("Bearer token must contain at least 32 characters")
        self.worker = worker
        self._token = resolved_token
        self.max_body_bytes = max_body_bytes
        super().__init__((host, port), _handler_type(self))


def _validate_bind_host(host: str, *, allow_remote: bool) -> None:
    """Forbid every non-loopback address unless an explicit opt-in is given."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("host must be an explicit IP address") from exc
    if not address.is_loopback and not allow_remote:
        raise ValueError("non-loopback binding requires allow_remote=True")


def _handler_type(server: WorkerHttpServer) -> type[BaseHTTPRequestHandler]:
    class WorkerRequestHandler(BaseHTTPRequestHandler):
        server: WorkerHttpServer
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            if not self._authenticated():
                return
            if self.path != "/health":
                self._write_error(HTTPStatus.NOT_FOUND, "not_found")
                return
            self._write_json(HTTPStatus.OK, {"status": "ok"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authenticated():
                return
            if self.path != "/v1/action":
                self._write_error(HTTPStatus.NOT_FOUND, "not_found")
                return
            payload = self._read_json_body()
            if payload is None:
                return
            # No other action schema is accepted until its safety policy exists.
            if set(payload) != {"action"} or payload["action"] != ActionKind.INVENTORY.value:
                self._write_error(HTTPStatus.BAD_REQUEST, "only_inventory_is_supported")
                return
            result = self.server.worker.execute(ActionRequest(kind=ActionKind.INVENTORY))
            self._write_json(
                HTTPStatus.OK,
                {
                    "action": result.kind.value,
                    "performed": result.performed,
                    "message": result.message,
                    "windows": [asdict(item) for item in result.windows],
                    "audit": result.audit,
                },
            )

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def _authenticated(self) -> bool:
            header = self.headers.get("Authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            if not supplied or not hmac.compare_digest(supplied, self.server._token):
                self.send_response(HTTPStatus.UNAUTHORIZED)
                self.send_header("WWW-Authenticate", "Bearer")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def _read_json_body(self) -> dict[str, object] | None:
            content_type = self.headers.get_content_type()
            if content_type != "application/json":
                self._write_error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "content_type_must_be_application_json"
                )
                return None
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError:
                length = -1
            if length < 0:
                self._write_error(HTTPStatus.LENGTH_REQUIRED, "content_length_required")
                return None
            if length > self.server.max_body_bytes:
                self._write_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large")
                return None
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write_error(HTTPStatus.BAD_REQUEST, "invalid_json")
                return None
            if not isinstance(data, dict):
                self._write_error(HTTPStatus.BAD_REQUEST, "json_object_required")
                return None
            return data

        def _method_not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, POST")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _write_error(self, status: HTTPStatus, code: str) -> None:
            self._write_json(status, {"error": code})

        def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Avoid emitting request headers and bearer tokens through stdio logs."""
            del format, args

    return WorkerRequestHandler
