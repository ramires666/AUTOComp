"""Authenticated HTTP facade for the allowlisted KV STUDIO UI worker.

The transport deliberately exposes structured worker actions only.  There is
no route capable of accepting command lines, process launches, filesystem
paths, or input outside the fixed operation and pinned-window allowlists.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import socket
import sys
import threading
import uuid
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

from .models import ActionKind, ActionRequest, action_request_from_payload
from .service import KVStudioWorker

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_MAX_BODY_BYTES: Final = 16 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 15.0
MAX_AUDIT_TEXT_LENGTH: Final = 1024
TOKEN_ENVIRONMENT_VARIABLE: Final = "AUTOCOMP_WORKER_TOKEN"
API_VERSION: Final = "1"


class WorkerHttpServer(ThreadingHTTPServer):
    """A bounded, authenticated transport for structured worker requests."""

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
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        audit_log_path: str | os.PathLike[str] | None = None,
    ) -> None:
        bind_address = _validate_bind_host(host, allow_remote=allow_remote)
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        if not 1 <= request_timeout_seconds <= 300:
            raise ValueError("request_timeout_seconds must be between 1 and 300")
        resolved_token = token if token is not None else os.getenv(TOKEN_ENVIRONMENT_VARIABLE)
        if not resolved_token:
            raise ValueError(
                f"Bearer token must be supplied or set in {TOKEN_ENVIRONMENT_VARIABLE}"
            )
        if (
            len(resolved_token) < 32
            or len(resolved_token) > 512
            or not resolved_token.isascii()
            or resolved_token != resolved_token.strip()
            or any(ord(character) < 33 or ord(character) == 127 for character in resolved_token)
        ):
            raise ValueError("Bearer token must contain 32-512 printable non-whitespace characters")
        self.worker = worker
        self._token = resolved_token
        self.max_body_bytes = max_body_bytes
        self.request_timeout_seconds = request_timeout_seconds
        # UI Automation is not re-entrant.  Reject concurrent UI requests
        # instead of allowing two remote callers to race the same editor.
        self.worker_lock = threading.Lock()
        self.audit_lock = threading.Lock()
        self.audit_log_path = Path(audit_log_path) if audit_log_path is not None else None
        self.address_family = socket.AF_INET6 if bind_address.version == 6 else socket.AF_INET
        super().__init__((host, port), _handler_type(self))

    def append_audit(self, record: dict[str, object]) -> None:
        """Durably append one bounded record without logging request headers."""
        if self.audit_log_path is None:
            raise RuntimeError("audit log is not configured")
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > self.max_body_bytes:
            raise ValueError("audit record exceeds the configured size limit")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        with self.audit_lock:
            descriptor = os.open(self.audit_log_path, flags, 0o600)
            try:
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("audit append made no progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def handle_error(self, request: object, client_address: object) -> None:
        """Ignore routine tunnel disconnects without dumping local paths to logs."""
        exception = sys.exc_info()[1]
        if isinstance(exception, (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]


def _validate_bind_host(
    host: str, *, allow_remote: bool
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Forbid every non-loopback address unless an explicit opt-in is given."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("host must be an explicit IP address") from exc
    if not address.is_loopback and not allow_remote:
        raise ValueError("non-loopback binding requires allow_remote=True")
    return address


def _serialise(value: object) -> object:
    """Convert worker-owned dataclasses/enums to JSON-compatible values."""
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)  # type: ignore[arg-type]
    if isinstance(value, dict):
        return {str(key): _serialise(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialise(item) for item in value]
    return value


def _parse_action_request(payload: dict[str, object]) -> ActionRequest:
    """Delegate JSON parsing to the typed worker-action allowlist."""
    return action_request_from_payload(payload)


def _result_payload(result: object) -> dict[str, object]:
    payload = _serialise(result)
    if not isinstance(payload, dict):
        raise TypeError("worker result must be a dataclass or dictionary")
    kind = payload.pop("kind", None)
    if kind is not None:
        payload["action"] = getattr(kind, "value", kind)
    return payload


def _bounded_text(value: object) -> str:
    text = str(value)
    if len(text) <= MAX_AUDIT_TEXT_LENGTH:
        return text
    return text[: MAX_AUDIT_TEXT_LENGTH - 1] + "…"


def _audit_request(request: ActionRequest, *, request_id: str, phase: str) -> dict[str, object]:
    kind = getattr(request.kind, "value", request.kind)
    record: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": request_id,
        "phase": phase,
        "action": _bounded_text(kind),
        "apply": bool(getattr(request, "apply", False)),
    }
    for field_name in (
        "checkpoint",
        "expected_source",
        "target",
        "expected_title",
    ):
        value = getattr(request, field_name, "")
        if value:
            record[field_name] = _bounded_text(value)
    operation = getattr(request, "desktop_operation", None) or getattr(
        request, "operation", None
    )
    if operation is not None:
        record["operation"] = _bounded_text(getattr(operation, "value", operation))
    text_value = getattr(request, "text", "")
    if text_value:
        # Audit the fact and size of typing without persisting possible secrets.
        record["text_length"] = len(text_value)
    for field_name in ("window_handle", "expected_pid", "x", "y", "delta"):
        value = getattr(request, field_name, None)
        if value not in {None, 0}:
            record[field_name] = value
    for field_name in ("target_path", "expected_path", "locator"):
        value = getattr(request, field_name, ())
        if value:
            record[field_name] = [_bounded_text(item) for item in tuple(value)[:64]]
    return record


def _handler_type(server: WorkerHttpServer) -> type[BaseHTTPRequestHandler]:
    class WorkerRequestHandler(BaseHTTPRequestHandler):
        server: WorkerHttpServer
        protocol_version = "HTTP/1.1"
        server_version = "AUTOCompWorker"
        sys_version = ""

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(self.server.request_timeout_seconds)

        def do_GET(self) -> None:  # noqa: N802
            if not self._authenticated():
                return
            if self.path == "/health":
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": "autocomp-worker",
                        "api_version": API_VERSION,
                        "mode": "offline",
                    },
                )
                return
            if self.path == "/v1/capabilities":
                self._write_json(HTTPStatus.OK, self._capabilities())
                return
            if self.path == "/v1/status":
                self._worker_status()
                return
            self._write_error(HTTPStatus.NOT_FOUND, "not_found")

        def do_POST(self) -> None:  # noqa: N802
            if not self._authenticated():
                return
            if self.path != "/v1/action":
                self._write_error(HTTPStatus.NOT_FOUND, "not_found")
                return
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                request = _parse_action_request(payload)
            except (TypeError, ValueError):
                self._write_error(HTTPStatus.BAD_REQUEST, "invalid_action_request")
                return
            result = self._execute_worker(request)
            if result is not None:
                response = _result_payload(result)
                response["request_id"] = self._worker_request_id
                self._write_json(HTTPStatus.OK, response)

        def do_HEAD(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def _capabilities(self) -> dict[str, object]:
            supported_names = (
                "STATUS",
                "INVENTORY",
                "EXPAND_TREE_ITEM",
                "INVENTORY_PROJECT_TREE",
                "PROBE_TREE_ITEM_RENAME",
                "RENAME_TREE_ITEM",
                "INSPECT_TREE_ITEM_MENU",
                "VISUAL_SNAPSHOT",
                "VISUAL_INPUT",
            )
            if self.server.worker.desktop_available:
                supported_names += (
                    "DESKTOP_WINDOWS",
                    "DESKTOP_SNAPSHOT",
                    "DESKTOP_INPUT",
                )
            actions = [
                member.value
                for name in supported_names
                if (member := getattr(ActionKind, name, None)) is not None
            ]
            mutating = [
                member.value
                for name in (
                    "EXPAND_TREE_ITEM",
                    "INVENTORY_PROJECT_TREE",
                    "PROBE_TREE_ITEM_RENAME",
                    "RENAME_TREE_ITEM",
                    "INSPECT_TREE_ITEM_MENU",
                    "VISUAL_INPUT",
                    "DESKTOP_INPUT",
                )
                if (member := getattr(ActionKind, name, None)) is not None
                and member.value in actions
            ]
            return {
                "service": "autocomp-worker",
                "api_version": API_VERSION,
                "mode": "offline",
                "authentication": "bearer",
                "actions": actions,
                "mutating_actions": mutating,
                "post_action_audit": {
                    "required": True,
                    "configured": self.server.audit_log_path is not None,
                },
                "arbitrary_shell": False,
                "arbitrary_input": False,
                "process_launch": False,
                "constrained_desktop_input": self.server.worker.desktop_available,
                "plc_operations": False,
            }

        def _worker_status(self) -> None:
            status_kind = getattr(ActionKind, "STATUS", ActionKind.INVENTORY)
            result = self._execute_worker(ActionRequest(kind=status_kind), audit=False)
            if result is not None:
                self._write_json(
                    HTTPStatus.OK,
                    {"status": "ok", "worker": _result_payload(result)},
                )

        def _execute_worker(
            self,
            request: ActionRequest,
            *,
            audit: bool = True,
        ) -> object | None:
            request_id = uuid.uuid4().hex
            self._worker_request_id = request_id
            if not self.server.worker_lock.acquire(blocking=False):
                if audit:
                    self._append_rejection_audit(request, request_id, "worker_busy")
                self._write_error(HTTPStatus.CONFLICT, "worker_busy")
                return None
            try:
                if audit and self.server.audit_log_path is None:
                    self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "audit_not_configured")
                    return None
                if audit:
                    try:
                        self.server.append_audit(
                            _audit_request(request, request_id=request_id, phase="intent")
                        )
                    except (OSError, RuntimeError, ValueError):
                        # Most importantly, an apply request cannot reach the UI
                        # unless its durable intent has already been recorded.
                        self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "audit_unavailable")
                        return None
                try:
                    result = self.server.worker.execute(request)
                except ValueError:
                    if audit:
                        self._append_outcome_audit(
                            request,
                            request_id,
                            error="invalid_action_request",
                        )
                    self._write_error(HTTPStatus.BAD_REQUEST, "invalid_action_request")
                    return None
                except Exception:
                    # UI Automation exceptions can contain window text and local
                    # implementation details.  Keep the remote error fail-closed.
                    if audit:
                        self._append_outcome_audit(
                            request,
                            request_id,
                            error="worker_unavailable",
                        )
                    self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "worker_unavailable")
                    return None
                if audit and not self._append_outcome_audit(request, request_id, result=result):
                    self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "audit_unavailable")
                    return None
                return result
            finally:
                self.server.worker_lock.release()

        def _append_rejection_audit(
            self,
            request: ActionRequest,
            request_id: str,
            error: str,
        ) -> None:
            if self.server.audit_log_path is None:
                return
            record = _audit_request(request, request_id=request_id, phase="rejected")
            record["error"] = error
            with suppress(OSError, RuntimeError, ValueError):
                self.server.append_audit(record)

        def _append_outcome_audit(
            self,
            request: ActionRequest,
            request_id: str,
            *,
            result: object | None = None,
            error: str = "",
        ) -> bool:
            try:
                record = _audit_request(request, request_id=request_id, phase="outcome")
                if error:
                    record["error"] = error
                if result is not None:
                    payload = _result_payload(result)
                    record["performed"] = bool(payload.get("performed", False))
                    for field_name in (
                        "before",
                        "after",
                        "rollback_attempted",
                        "rollback_succeeded",
                    ):
                        value = payload.get(field_name)
                        if value not in {None, "", False}:
                            record[field_name] = (
                                value if isinstance(value, bool) else _bounded_text(value)
                            )
                self.server.append_audit(record)
            except (OSError, RuntimeError, TypeError, ValueError):
                return False
            return True

        def _authenticated(self) -> bool:
            header = self.headers.get("Authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            if (
                not supplied
                or not supplied.isascii()
                or not hmac.compare_digest(supplied, self.server._token)
            ):
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
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _write_error(self, status: HTTPStatus, code: str) -> None:
            self._write_json(status, {"error": code})

        def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Avoid emitting request headers and bearer tokens through stdio logs."""
            del format, args

    return WorkerRequestHandler
