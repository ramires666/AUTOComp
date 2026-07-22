"""Application-agnostic vision controller for the AUTOComp desktop worker.

The remote worker remains deliberately dumb: it enumerates windows, captures a
pinned window and executes a bounded input sequence.  This controller owns the
mission, asks an OpenAI-compatible VLM what to do next, validates the decision,
and durably records every observation and action so an interrupted run resumes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "universal-windows-agent.md"
NORMALIZED_COORDINATE_MAX = 1000

OPERATIONS = (
    "click",
    "right_click",
    "double_click",
    "wheel",
    "type_text",
    "key_enter",
    "key_escape",
    "key_ctrl_a",
    "key_ctrl_c",
    "key_ctrl_d",
    "key_ctrl_home",
    "key_ctrl_shift_end",
    "key_f2",
    "tab",
    "shift_tab",
)

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["select_window", "input", "wait", "done", "failed"],
        },
        "window_index": {"type": ["integer", "null"]},
        "operations": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": list(OPERATIONS)},
                    "x": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "maximum": NORMALIZED_COORDINATE_MAX,
                    },
                    "y": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "maximum": NORMALIZED_COORDINATE_MAX,
                    },
                    "delta": {"type": ["integer", "null"]},
                    "text": {"type": ["string", "null"]},
                    "pause_ms": {"type": "integer", "minimum": 0, "maximum": 1000},
                },
                "required": ["operation", "x", "y", "delta", "text", "pause_ms"],
                "additionalProperties": False,
            },
        },
        "wait_seconds": {"type": ["number", "null"], "minimum": 0, "maximum": 10},
        "reason": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": [
        "kind",
        "window_index",
        "operations",
        "wait_seconds",
        "reason",
        "evidence",
    ],
    "additionalProperties": False,
}

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "autocomp_desktop_decision",
        "strict": True,
        "schema": DECISION_SCHEMA,
    },
}

MISSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"const": 1},
        "objective": {"type": "string", "minLength": 1},
        "context": {"type": "string"},
        "goals": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "context": {"type": "string"},
                    "success_criteria": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "allowed_text": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "original": {"type": "string", "minLength": 1},
                                "english": {"type": ["string", "null"]},
                                "russian": {"type": ["string", "null"]},
                            },
                            "required": ["original", "english", "russian"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "objective", "context", "success_criteria", "allowed_text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["schema_version", "objective", "context", "goals"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Settings:
    worker_endpoint: str
    worker_token: str
    llm_endpoint: str
    llm_key: str
    llm_model: str


def _dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[name.strip()] = value
    return result


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str = "",
    timeout: float = 45,
) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        method = "POST"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request failed: {url}: {exc}") from exc


def _load_settings(worker_env: Path, llm_env: Path) -> Settings:
    values = {**_dotenv(llm_env), **_dotenv(worker_env), **os.environ}
    worker_endpoint = values.get("AUTOCOMP_WORKER_ENDPOINT", "").rstrip("/")
    worker_token = values.get("AUTOCOMP_WORKER_TOKEN", "")
    llm_endpoint = values.get("AUTOCOMP_LLM_ENDPOINT", "http://127.0.0.1:8080/v1").rstrip(
        "/"
    )
    model = values.get("AUTOCOMP_LLM_MODEL", "auto") or "auto"
    if not worker_endpoint or not worker_token:
        raise RuntimeError("AUTOCOMP_WORKER_ENDPOINT and AUTOCOMP_WORKER_TOKEN are required")
    if model.casefold() == "auto":
        response = _request_json(
            f"{llm_endpoint}/models",
            token=values.get("AUTOCOMP_LLM_API_KEY", ""),
            timeout=10,
        )
        entries = response.get("data", []) if isinstance(response, dict) else []
        model_ids = [item.get("id") for item in entries if isinstance(item, dict)]
        model = next((str(item) for item in model_ids if item), "")
        if not model:
            raise RuntimeError("local LLM endpoint advertised no model")
    return Settings(
        worker_endpoint=worker_endpoint,
        worker_token=worker_token,
        llm_endpoint=llm_endpoint,
        llm_key=values.get("AUTOCOMP_LLM_API_KEY", ""),
        llm_model=model,
    )


def _worker(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    result = _request_json(
        f"{settings.worker_endpoint}/v1/action",
        payload=payload,
        token=settings.worker_token,
        timeout=30,
    )
    if not isinstance(result, dict):
        raise RuntimeError("worker returned a non-object response")
    return result


def _windows(settings: Settings) -> list[dict[str, Any]]:
    response = _worker(settings, {"action": "desktop_windows"})
    raw = response.get("desktop_windows")
    if not isinstance(raw, list):
        raise RuntimeError("worker returned no desktop_windows array")
    result = []
    for item in raw:
        if not isinstance(item, dict) or item.get("minimized"):
            continue
        bounds = item.get("bounds")
        if (
            not isinstance(bounds, list)
            or len(bounds) != 4
            or int(bounds[2]) <= int(bounds[0])
            or int(bounds[3]) <= int(bounds[1])
        ):
            continue
        if not item.get("title") and not item.get("foreground") and not item.get("owner_handle"):
            continue
        result.append(item)
    return result


def _worker_handshake(settings: Settings) -> dict[str, Any]:
    health = _request_json(
        f"{settings.worker_endpoint}/health", token=settings.worker_token, timeout=15
    )
    capabilities = _request_json(
        f"{settings.worker_endpoint}/v1/capabilities",
        token=settings.worker_token,
        timeout=15,
    )
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise RuntimeError("worker health handshake failed")
    if not isinstance(capabilities, dict):
        raise RuntimeError("worker returned invalid capabilities")
    actions = capabilities.get("actions", [])
    required = {"desktop_windows", "desktop_snapshot", "desktop_input_sequence"}
    if not isinstance(actions, list) or not required.issubset(set(actions)):
        raise RuntimeError("worker lacks universal desktop capabilities")
    build_id = capabilities.get("build_id") or health.get("build_id")
    if not isinstance(build_id, str) or not build_id:
        raise RuntimeError("worker is too old: build_id is missing")
    return {
        "build_id": build_id,
        "boot_id": capabilities.get("boot_id") or health.get("boot_id"),
        "started_at": capabilities.get("started_at") or health.get("started_at"),
        "operation_limits": capabilities.get("operation_limits", {}),
    }


def _window_identity(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "handle": int(window["handle"]),
        "process_id": int(window["process_id"]),
        "title": str(window["title"]),
    }


def _matching_window(
    selected: dict[str, Any] | None, windows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not selected:
        return None
    for window in windows:
        if _window_identity(window) == selected:
            return window
    return None


def _foreground_route(
    selected: dict[str, Any] | None, windows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Route an app-owned foreground popup before the model can click through it."""
    if selected is None:
        return None
    selected_handle = int(selected["handle"])
    selected_pid = int(selected["process_id"])
    selected_enabled = bool(selected.get("enabled", True))
    for window in windows:
        direct_owned_popup = int(window.get("owner_handle", 0)) == selected_handle
        if (
            int(window["handle"]) != selected_handle
            and int(window["process_id"]) == selected_pid
            and bool(window.get("enabled", True))
            and (
                direct_owned_popup
                or (
                    bool(window.get("foreground"))
                    and (int(window.get("owner_handle", 0)) > 0 or not selected_enabled)
                )
            )
        ):
            return window
    return None


def _same_process_foreground(
    previous_identity: dict[str, Any], windows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    previous_pid = int(previous_identity["process_id"])
    return next(
        (
            window
            for window in windows
            if int(window["process_id"]) == previous_pid
            and bool(window.get("foreground"))
            and bool(window.get("enabled", True))
        ),
        None,
    )


def _snapshot(settings: Settings, window: dict[str, Any]) -> dict[str, Any]:
    identity = _window_identity(window)
    response = _worker(
        settings,
        {
            "action": "desktop_snapshot",
            "window_handle": identity["handle"],
            "expected_pid": identity["process_id"],
            "expected_title": identity["title"],
        },
    )
    snapshot = response.get("desktop_snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("png_base64"):
        raise RuntimeError("worker returned no PNG desktop snapshot")
    return snapshot


def _extract_content(completion: Any) -> object:
    try:
        content = completion["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("completion has no message content") from exc
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        content = "".join(str(item) for item in text_parts)
    if not isinstance(content, str):
        raise ValueError("message content is not JSON text or object")
    fenced = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content, flags=re.I)
    try:
        return json.loads(fenced)
    except json.JSONDecodeError:
        start, end = fenced.find("{"), fenced.rfind("}")
        if start < 0 or end < start:
            raise ValueError("message contains no JSON object") from None
        return json.loads(fenced[start : end + 1])


def _validate_decision(
    value: object,
    *,
    windows: list[dict[str, Any]],
    frame: dict[str, Any] | None,
    allowed_type_text: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(DECISION_SCHEMA["required"]):
        raise ValueError("decision has missing or unexpected fields")
    kind = value.get("kind")
    if kind not in {"select_window", "input", "wait", "done", "failed"}:
        raise ValueError("decision kind is unsupported")
    if not isinstance(value.get("reason"), str) or not isinstance(value.get("evidence"), str):
        raise ValueError("decision reason/evidence must be strings")
    window_index = value.get("window_index")
    operations = value.get("operations")
    wait_seconds = value.get("wait_seconds")
    if not isinstance(operations, list) or len(operations) > 8:
        raise ValueError("operations must be an array of at most 8 items")
    if kind == "select_window":
        if isinstance(window_index, bool) or not isinstance(window_index, int):
            raise ValueError("select_window requires an integer window_index")
        if window_index < 0 or window_index >= len(windows):
            raise ValueError("window_index is outside the current window list")
        if operations or wait_seconds is not None:
            raise ValueError("select_window must not contain operations or wait_seconds")
    elif window_index is not None:
        raise ValueError(f"{kind} must use null window_index")
    if kind == "input":
        if frame is None or not 1 <= len(operations) <= 8:
            raise ValueError("input requires a frame and 1 to 8 operations")
        if wait_seconds is not None:
            raise ValueError("input must use null wait_seconds")
        for index, operation in enumerate(operations):
            _validate_operation(
                operation,
                index=index,
                frame=frame,
                allowed_type_text=allowed_type_text,
            )
        _validate_operation_sequence(operations)
    elif kind != "select_window" and operations:
        raise ValueError(f"{kind} must not contain operations")
    if kind == "wait":
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)):
            raise ValueError("wait requires numeric wait_seconds")
        if not 0 <= wait_seconds <= 10:
            raise ValueError("wait_seconds must be between 0 and 10")
    elif wait_seconds is not None:
        raise ValueError(f"{kind} must use null wait_seconds")
    return value


def _validate_operation(
    value: object,
    *,
    index: int,
    frame: dict[str, Any],
    allowed_type_text: set[str] | None,
) -> None:
    required = {"operation", "x", "y", "delta", "text", "pause_ms"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"operations[{index}] has missing or unexpected fields")
    operation = value.get("operation")
    if operation not in OPERATIONS:
        raise ValueError(f"operations[{index}] operation is unsupported")
    pause = value.get("pause_ms")
    if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= 1000:
        raise ValueError(f"operations[{index}] pause_ms is invalid")
    x, y, delta, text = (value.get(name) for name in ("x", "y", "delta", "text"))
    coordinate = operation in {"click", "right_click", "double_click", "wheel"}
    if coordinate:
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (x, y)):
            raise ValueError(f"operations[{index}] requires integer x/y")
        if not 0 <= x <= NORMALIZED_COORDINATE_MAX or not 0 <= y <= NORMALIZED_COORDINATE_MAX:
            raise ValueError(f"operations[{index}] normalized coordinates are outside 0..1000")
    elif x is not None or y is not None:
        raise ValueError(f"operations[{index}] must use null x/y")
    if operation == "wheel":
        if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0 or abs(delta) > 12:
            raise ValueError(f"operations[{index}] wheel delta must be -12..12 and nonzero")
    elif delta is not None:
        raise ValueError(f"operations[{index}] must use null delta")
    if operation == "type_text":
        if not isinstance(text, str) or not text or len(text) > 512:
            raise ValueError(f"operations[{index}] type_text text is invalid")
        if any(ord(character) < 32 or ord(character) == 127 for character in text):
            raise ValueError(f"operations[{index}] text contains control characters")
        if allowed_type_text and text not in allowed_type_text:
            raise ValueError(f"operations[{index}] text is not allowlisted by the active goal")
    elif text is not None:
        raise ValueError(f"operations[{index}] must use null text")


def _validate_operation_sequence(operations: list[dict[str, Any]]) -> None:
    """Keep UI transitions observable; only text replacement may be atomic."""
    if len(operations) <= 1:
        return
    names = [str(item["operation"]) for item in operations]
    basic_replacement = names in (
        ["key_ctrl_a", "type_text"],
        ["click", "type_text"],
        ["click", "key_ctrl_a", "type_text"],
    )
    verified_tab_replacement = (
        names[-2:] == ["key_ctrl_a", "type_text"]
        and 1 <= len(names[:-2]) <= 6
        and all(name == "tab" for name in names[:-2])
    )
    if not (basic_replacement or verified_tab_replacement):
        raise ValueError(
            "multi-operation input may only replace a focused/clicked field, optionally "
            "after one to six mission-verified Tab steps; menus, scrolling, new dialogs, "
            "and confirmation require a fresh frame"
        )


def _prompt(
    *,
    mission: dict[str, Any],
    goal: dict[str, Any],
    windows: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    state: dict[str, Any],
    frame: dict[str, Any] | None,
    validation_error: str = "",
) -> str:
    compact_windows = [
        {
            "index": index,
            "title": item.get("title", ""),
            "process_id": item.get("process_id"),
            "bounds": item.get("bounds"),
            "minimized": item.get("minimized"),
            "owner_handle": item.get("owner_handle", 0),
            "foreground": item.get("foreground", False),
            "enabled": item.get("enabled", True),
            "class_name": item.get("class_name", ""),
        }
        for index, item in enumerate(windows)
    ]
    recent = state.get("events", [])[-8:]
    frame_note = (
        f"Attached image is the exact selected-window client frame, {frame['width']}x"
        f"{frame['height']} pixels; return x/y normalized to 0..1000 across this full frame."
        if frame
        else "No screenshot is attached because no current window is selected."
    )
    correction = (
        f"Your previous response was rejected: {validation_error}\n"
        if validation_error
        else ""
    )
    translations = goal["allowed_text"]
    return f"""You are the visual controller of a generic Windows computer.
You have no application-specific automation and must work from the current visible pixels.
The remote executor only performs the strict actions you return; it cannot reason.

OVERALL MISSION:
{mission['objective']}
Context: {mission['context']}

ACTIVE GOAL (complete only this goal now):
ID: {goal['id']}
Objective: {goal['objective']}
Context: {goal['context']}
Success criteria: {json.dumps(goal['success_criteria'], ensure_ascii=False)}
Approved text/translation records: {json.dumps(translations, ensure_ascii=False)}

Current windows: {json.dumps(compact_windows, ensure_ascii=False)}
Selected window identity: {json.dumps(selected, ensure_ascii=False)}
{frame_note}
Recent durable events: {json.dumps(recent, ensure_ascii=False)}
{correction}
Choose the next smallest reliable step:
- select_window may choose any current visible window by its listed index, including a modal.
- input normally contains one operation. The only allowed atomic multi-operation input is
  click field, optional Ctrl+A, then type exact text. Confirmation is a later turn after a
  fresh frame. Do not assume focus that is not visible.
- when approved text records are non-empty, type_text may use only an exact non-null value
  present in original/english/russian. Never paraphrase, truncate, or invent translation text.
- inspect the full fresh screenshot before clicking. Return normalized 0..1000 coordinates,
  not internal resized-image pixels.
- after input, use the next fresh screenshot to verify the visible result.
- if an action had no visible effect, change strategy instead of repeating it blindly.
- return done only when this active goal's success criteria are visibly proven. The controller
  will durably mark it complete and then present the next goal.
- do not launch processes, use a shell, connect to hardware, or perform online/transfer actions.

Return only the JSON object required by the supplied strict schema. Every field is required;
use null and [] for fields irrelevant to the chosen kind. Keep reason and evidence concise.
"""


def _model_decision(
    settings: Settings,
    *,
    prompt: str,
    frame: dict[str, Any] | None,
    windows: list[dict[str, Any]],
    allowed_type_text: set[str] | None,
    system_prompt: str,
    retries: int = 3,
) -> dict[str, Any]:
    validation_error = ""
    use_response_format = True
    for _ in range(retries):
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt + validation_error}]
        if frame:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{frame['png_base64']}"},
                }
            )
        body: dict[str, Any] = {
            "model": settings.llm_model,
            "temperature": 0,
            "max_tokens": 512,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if use_response_format:
            body["response_format"] = RESPONSE_FORMAT
        try:
            completion = _request_json(
                f"{settings.llm_endpoint}/chat/completions",
                payload=body,
                token=settings.llm_key,
                timeout=45,
            )
        except RuntimeError as exc:
            backend_error = str(exc).casefold()
            grammar_rejected = any(
                marker in backend_error
                for marker in ("http 400", "grammar", "sampler", "response_format")
            )
            if use_response_format and grammar_rejected:
                use_response_format = False
                body.pop("response_format", None)
                completion = _request_json(
                    f"{settings.llm_endpoint}/chat/completions",
                    payload=body,
                    token=settings.llm_key,
                    timeout=45,
                )
            else:
                raise
        try:
            return _validate_decision(
                _extract_content(completion),
                windows=windows,
                frame=frame,
                allowed_type_text=allowed_type_text,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            validation_error = (
                f"\nYour prior response was invalid ({exc}). Return one corrected JSON object."
            )
    raise RuntimeError(
        f"vision model returned invalid decisions {retries} times:{validation_error}"
    )


def _input_payload(
    *,
    window: dict[str, Any],
    frame: dict[str, Any],
    operations: list[dict[str, Any]],
    checkpoint: str,
) -> dict[str, Any]:
    identity = _window_identity(window)
    clean_operations = []
    for operation in operations:
        worker_operation = {
            "right_click": "right",
            "double_click": "double",
        }.get(operation["operation"], operation["operation"])
        clean = {"operation": worker_operation, "pause_ms": operation["pause_ms"]}
        if operation["x"] is not None:
            width = int(frame["width"])
            height = int(frame["height"])
            clean.update(
                x=min(width - 1, round(operation["x"] * (width - 1) / 1000)),
                y=min(height - 1, round(operation["y"] * (height - 1) / 1000)),
            )
        if operation["delta"] is not None:
            clean["delta"] = operation["delta"]
        if operation["text"] is not None:
            clean["text"] = operation["text"]
        clean_operations.append(clean)
    return {
        "action": "desktop_input_sequence",
        "window_handle": identity["handle"],
        "expected_pid": identity["process_id"],
        "expected_title": identity["title"],
        "checkpoint": checkpoint,
        "operations": clean_operations,
        "apply": True,
    }


def _input_signature(
    *, frame: dict[str, Any], window: dict[str, Any], operations: list[dict[str, Any]]
) -> str:
    value = {
        "coordinate_contract": "normalized-0-1000-v1",
        "frame_sha256": frame.get("png_sha256", ""),
        "window": _window_identity(window),
        "operations": operations,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _input_attempt_count(state: dict[str, Any], signature: str) -> int:
    return sum(
        event.get("event") == "decision"
        and event.get("phase") == "intent"
        and event.get("input_signature") == signature
        for event in state.get("events", [])
    )


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _mission_id(mission: dict[str, Any]) -> str:
    canonical = json.dumps(mission, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _load_state(
    path: Path, mission: dict[str, Any], *, prompt_sha256: str = ""
) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema_version") != 1 or state.get("mission") != mission:
            raise RuntimeError("state file belongs to a different mission or schema")
        if state.get("status") == "completed":
            return state
        if state.get("prompt_sha256") != prompt_sha256:
            state.setdefault("prompt_versions", []).append(prompt_sha256)
            state["prompt_sha256"] = prompt_sha256
        state["status"] = "running"
        return state
    return {
        "schema_version": 1,
        "mission_id": _mission_id(mission),
        "mission": mission,
        "prompt_sha256": prompt_sha256,
        "prompt_versions": [prompt_sha256],
        "status": "running",
        "selected_window": None,
        "active_goal_index": 0,
        "goals": [
            {"id": goal["id"], "status": "pending", "result": None}
            for goal in mission["goals"]
        ],
        "events": [],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }


def _append_event(state: dict[str, Any], event: dict[str, Any], state_path: Path) -> None:
    state["events"].append(event)
    state["updated_at"] = int(time.time())
    _write_state(state_path, state)


def _save_frame(state_path: Path, step: int, frame: dict[str, Any]) -> str:
    encoded = frame.get("png_base64")
    expected_hash = frame.get("png_sha256")
    if not isinstance(encoded, str) or not isinstance(expected_hash, str):
        raise RuntimeError("worker frame is missing PNG evidence metadata")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("worker frame contains invalid base64") from exc
    if hashlib.sha256(png).hexdigest() != expected_hash:
        raise RuntimeError("worker frame hash does not match PNG evidence")
    directory = state_path.parent / f"{state_path.stem}-frames"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{step:05d}-{expected_hash[:12]}.png"
    if not destination.exists():
        fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(png)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise
    return str(destination)


def _allowed_type_text(goal: dict[str, Any]) -> set[str] | None:
    translations = goal["allowed_text"]
    if not translations:
        return None
    return {
        text
        for translation in translations
        for text in (
            translation["original"],
            translation["english"],
            translation["russian"],
        )
        if text
    }


def _validate_mission(value: object) -> dict[str, Any]:
    required = {"schema_version", "objective", "context", "goals"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("mission has missing or unexpected fields")
    if value.get("schema_version") != 1:
        raise ValueError("mission schema_version must equal 1")
    for field in ("objective", "context"):
        text = value.get(field)
        if not isinstance(text, str) or (field == "objective" and not text.strip()):
            raise ValueError(f"mission {field} is invalid")
    goals = value.get("goals")
    if not isinstance(goals, list) or not goals:
        raise ValueError("mission goals must be a non-empty array")
    goal_fields = {"id", "objective", "context", "success_criteria", "allowed_text"}
    translation_fields = {"original", "english", "russian"}
    seen_ids: set[str] = set()
    for index, goal in enumerate(goals):
        if not isinstance(goal, dict) or set(goal) != goal_fields:
            raise ValueError(f"goals[{index}] has missing or unexpected fields")
        goal_id = goal.get("id")
        if (
            not isinstance(goal_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", goal_id)
            or goal_id in seen_ids
        ):
            raise ValueError(f"goals[{index}].id is invalid or duplicated")
        seen_ids.add(goal_id)
        for field in ("objective", "context"):
            text = goal.get(field)
            if not isinstance(text, str) or (field == "objective" and not text.strip()):
                raise ValueError(f"goals[{index}].{field} is invalid")
        criteria = goal.get("success_criteria")
        if (
            not isinstance(criteria, list)
            or not criteria
            or any(not isinstance(item, str) or not item.strip() for item in criteria)
        ):
            raise ValueError(f"goals[{index}].success_criteria is invalid")
        translations = goal.get("allowed_text")
        if not isinstance(translations, list):
            raise ValueError(f"goals[{index}].allowed_text must be an array")
        for translation_index, translation in enumerate(translations):
            if not isinstance(translation, dict) or set(translation) != translation_fields:
                raise ValueError(
                    f"goals[{index}].allowed_text[{translation_index}] has invalid fields"
                )
            for field in translation_fields:
                text = translation.get(field)
                if field == "original" and not isinstance(text, str):
                    raise ValueError(
                        f"goals[{index}].allowed_text[{translation_index}].original is invalid"
                    )
                if field != "original" and text is not None and (
                    not isinstance(text, str) or not text
                ):
                    raise ValueError(
                        f"goals[{index}].allowed_text[{translation_index}].{field} is invalid"
                    )
                if isinstance(text, str) and (
                    len(text) > 512
                    or any(ord(character) < 32 or ord(character) == 127 for character in text)
                ):
                    raise ValueError(
                        f"goals[{index}].allowed_text[{translation_index}].{field} is unsafe"
                    )
    return value


def run(
    settings: Settings,
    *,
    mission: dict[str, Any],
    state_path: Path,
    max_steps: int,
    system_prompt: str,
) -> int:
    prompt_sha256 = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    state = _load_state(state_path, mission, prompt_sha256=prompt_sha256)
    handshake = _worker_handshake(settings)
    print(
        json.dumps(
            {
                "event": "preflight_ok",
                "worker_build_id": handshake["build_id"],
                "worker_boot_id": handshake.get("boot_id"),
                "llm_model": settings.llm_model,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if state.get("worker_build_id") != handshake["build_id"]:
        state["worker_build_id"] = handshake["build_id"]
        state.setdefault("worker_build_history", []).append(handshake)
        _write_state(state_path, state)
    if state["status"] == "completed":
        print(f"Mission already completed; state: {state_path}", flush=True)
        return 0
    mission_id = str(state["mission_id"])
    for _ in range(max_steps):
        goal_index = int(state["active_goal_index"])
        if goal_index >= len(mission["goals"]):
            state["status"] = "completed"
            _write_state(state_path, state)
            return 0
        goal = mission["goals"][goal_index]
        state["goals"][goal_index]["status"] = "in_progress"
        step = len(state["events"]) + 1
        windows = _windows(settings)
        selected = _matching_window(state.get("selected_window"), windows)
        if state.get("selected_window") and selected is None:
            previous_identity = state["selected_window"]
            recovered = _same_process_foreground(previous_identity, windows)
            state["selected_window"] = (
                _window_identity(recovered) if recovered is not None else None
            )
            _append_event(
                state,
                {
                    "step": step,
                    "event": "selected_window_disappeared",
                    "previous_window": previous_identity,
                    "recovered_window": state["selected_window"],
                },
                state_path,
            )
            continue
        routed = _foreground_route(selected, windows)
        if routed is not None:
            previous_identity = state["selected_window"]
            state["selected_window"] = _window_identity(routed)
            _append_event(
                state,
                {
                    "step": step,
                    "event": "foreground_popup_routed",
                    "previous_window": previous_identity,
                    "selected_window": state["selected_window"],
                },
                state_path,
            )
            continue
        frame = _snapshot(settings, selected) if selected else None
        frame_path = _save_frame(state_path, step, frame) if frame else ""
        prompt = _prompt(
            mission=mission,
            goal=goal,
            windows=windows,
            selected=state.get("selected_window"),
            state=state,
            frame=frame,
            validation_error=str(state.get("validation_error", "")),
        )
        decision = _model_decision(
            settings,
            prompt=prompt,
            frame=frame,
            windows=windows,
            allowed_type_text=_allowed_type_text(goal),
            system_prompt=system_prompt,
        )
        event: dict[str, Any] = {
            "step": step,
            "event": "decision",
            "frame_sha256": frame.get("png_sha256", "") if frame else "",
            "frame_path": frame_path,
            "goal_id": goal["id"],
            "decision": decision,
        }
        kind = decision["kind"]
        if kind == "select_window":
            selected = windows[decision["window_index"]]
            state["selected_window"] = _window_identity(selected)
            event["selected_window"] = state["selected_window"]
        elif kind == "input":
            if selected is None:
                raise RuntimeError("validated input decision has no selected window")
            signature = _input_signature(
                frame=frame, window=selected, operations=decision["operations"]
            )
            event["input_signature"] = signature
            attempt_count = _input_attempt_count(state, signature)
            if attempt_count:
                blocked_count = int(state.get("blocked_repeat_count", 0)) + 1
                state["blocked_repeat_count"] = blocked_count
                state["validation_error"] = (
                    "The exact same input was already attempted on the identical frame. "
                    "It is blocked. Reinspect windows and pixels and choose a materially "
                    "different recovery action."
                )
                blocked_event = {
                    "step": step,
                    "event": "repeated_input_blocked",
                    "goal_id": goal["id"],
                    "frame_sha256": frame.get("png_sha256", ""),
                    "input_signature": signature,
                    "blocked_count": blocked_count,
                }
                _append_event(state, blocked_event, state_path)
                print(json.dumps(blocked_event, ensure_ascii=False), flush=True)
                if blocked_count >= 2:
                    state["status"] = "paused"
                    state["result"] = {
                        "reason": "model repeated a blocked input twice; operator review required"
                    }
                    _write_state(state_path, state)
                    return 4
                continue
            state.pop("validation_error", None)
            state["blocked_repeat_count"] = 0
            checkpoint = f"vision-{mission_id}-{step:05d}"
            event["phase"] = "intent"
            event["checkpoint"] = checkpoint
            _append_event(state, event, state_path)
            try:
                result = _worker(
                    settings,
                    _input_payload(
                        window=selected,
                        frame=frame,
                        operations=decision["operations"],
                        checkpoint=checkpoint,
                    ),
                )
            except RuntimeError as exc:
                outcome = {
                    "step": step,
                    "event": "worker_outcome",
                    "goal_id": goal["id"],
                    "checkpoint": checkpoint,
                    "error": str(exc)[:1000],
                }
                _append_event(state, outcome, state_path)
                print(json.dumps(outcome, ensure_ascii=False), flush=True)
                time.sleep(1)
                continue
            outcome = {
                "step": step,
                "event": "worker_outcome",
                "goal_id": goal["id"],
                "checkpoint": checkpoint,
                "worker": {
                "performed": result.get("performed"),
                "message": result.get("message"),
                "request_id": result.get("request_id"),
                },
            }
            if result.get("performed") is not True:
                outcome["worker_error"] = True
            _append_event(state, outcome, state_path)
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
            time.sleep(0.25)
            continue
        elif kind == "wait":
            time.sleep(float(decision["wait_seconds"]))
        elif kind == "done":
            state["goals"][goal_index]["status"] = "completed"
            state["goals"][goal_index]["result"] = {
                "reason": decision["reason"],
                "evidence": decision["evidence"],
            }
            state["active_goal_index"] = goal_index + 1
            if state["active_goal_index"] >= len(mission["goals"]):
                state["status"] = "completed"
                state["result"] = {"reason": "all goals completed"}
        elif kind == "failed":
            state["goals"][goal_index]["status"] = "failed"
            state["goals"][goal_index]["result"] = {
                "reason": decision["reason"],
                "evidence": decision["evidence"],
            }
            state["status"] = "failed"
        _append_event(state, event, state_path)
        print(json.dumps(event, ensure_ascii=False), flush=True)
        if kind == "failed":
            return 2
        if kind == "done" and state["status"] == "completed":
            return 0
        time.sleep(0.25)
    state["status"] = "paused"
    state["result"] = {"reason": "max_steps reached; resume with the same command"}
    _write_state(state_path, state)
    print(f"Paused after {max_steps} steps; resume state: {state_path}", flush=True)
    return 3


def _mission_spec(args: argparse.Namespace) -> dict[str, Any]:
    if bool(args.mission) == bool(args.mission_file):
        raise ValueError("provide exactly one of --mission or --mission-file")
    if args.mission:
        text = args.mission.strip()
        if not text:
            raise ValueError("mission must not be empty")
        return {
            "schema_version": 1,
            "objective": text,
            "context": "",
            "goals": [
                {
                    "id": "goal-001",
                    "objective": text,
                    "context": "",
                    "success_criteria": [text],
                    "allowed_text": [],
                }
            ],
        }
    mission_path = Path(args.mission_file)
    try:
        value = json.loads(mission_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError("--mission-file must contain a JSON mission object") from exc
    return _validate_mission(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal persisted VLM desktop mission loop")
    parser.add_argument("--mission")
    parser.add_argument("--mission-file")
    parser.add_argument("--state-file")
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--llm-env", default=".env")
    parser.add_argument("--prompt-file", default=str(DEFAULT_PROMPT))
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("explicit --apply is required")
    if args.max_steps <= 0:
        raise SystemExit("--max-steps must be positive")
    project = Path(__file__).resolve().parent.parent
    mission = _mission_spec(args)
    state_path = (
        Path(args.state_file)
        if args.state_file
        else project / ".autocomp" / f"vision-mission-{_mission_id(mission)}.json"
    )
    if not state_path.is_absolute():
        state_path = project / state_path
    settings = _load_settings(project / args.worker_env, project / args.llm_env)
    prompt_path = Path(args.prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = project / prompt_path
    system_prompt = prompt_path.read_text(encoding="utf-8-sig").strip()
    if not system_prompt:
        raise SystemExit("--prompt-file must not be empty")
    return run(
        settings,
        mission=mission,
        state_path=state_path,
        max_steps=args.max_steps,
        system_prompt=system_prompt,
    )


if __name__ == "__main__":
    raise SystemExit(main())
