"""Universal vision-guided Windows automation agent.

This controller is intentionally application-agnostic. It uses the remote AUTOComp
worker as generic eyes and hands: enumerate windows, capture pinned snapshots,
and send bounded desktop input. The local VLM decides what to do next based on
the screenshot and the user-supplied task.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "click",
                "right_click",
                "double_click",
                "wheel",
                "type_text",
                "key_enter",
                "key_escape",
                "key_f2",
                "key_ctrl_a",
                "wait",
                "done",
                "failed",
            ],
        },
        "x": {"type": ["integer", "null"]},
        "y": {"type": ["integer", "null"]},
        "delta": {"type": ["integer", "null"]},
        "text": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["action", "x", "y", "delta", "text", "reason"],
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
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _settings(worker_env: Path, llm_env: Path) -> Settings:
    values = {**_dotenv(llm_env), **_dotenv(worker_env)}
    for key, value in os.environ.items():
        if value:
            values[key] = value
    worker_endpoint = values.get("AUTOCOMP_WORKER_ENDPOINT", "").rstrip("/")
    worker_token = values.get("AUTOCOMP_WORKER_TOKEN", "")
    llm_endpoint = values.get("AUTOCOMP_LLM_ENDPOINT", "http://127.0.0.1:8080/v1").rstrip(
        "/"
    )
    llm_model = values.get("AUTOCOMP_LLM_MODEL", "auto") or "auto"
    if not worker_endpoint or not worker_token:
        raise RuntimeError(f"worker endpoint/token missing in {worker_env}")
    if llm_model.casefold() == "auto":
        payload = _json_request(
            f"{llm_endpoint}/models",
            key=values.get("AUTOCOMP_LLM_API_KEY", ""),
        )
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        models = [item.get("id") for item in entries if isinstance(item, dict) and item.get("id")]
        if not models:
            raise RuntimeError("local LLM endpoint returned no models")
        llm_model = str(models[0])
    return Settings(
        worker_endpoint=worker_endpoint,
        worker_token=worker_token,
        llm_endpoint=llm_endpoint,
        llm_key=values.get("AUTOCOMP_LLM_API_KEY", ""),
        llm_model=llm_model,
    )


def _json_request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    bearer: str | None = None,
    key: str | None = None,
    timeout: float = 30.0,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} -> HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"{url} -> {exc.reason}") from exc


def _worker(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    return _json_request(
        f"{settings.worker_endpoint}/v1/action",
        payload=payload,
        bearer=settings.worker_token,
    )


def _select_window(settings: Settings, title_contains: str) -> dict[str, Any]:
    result = _worker(settings, {"action": "desktop_windows"})
    windows = result.get("desktop_windows", [])
    for window in windows:
        if title_contains.casefold() in window.get("title", "").casefold():
            if window.get("minimized"):
                continue
            return window
    raise RuntimeError(f"no visible window contains {title_contains!r}")


def _snapshot(settings: Settings, window: dict[str, Any]) -> bytes:
    result = _worker(
        settings,
        {
            "action": "desktop_snapshot",
            "window_handle": window["handle"],
            "expected_pid": window["process_id"],
            "expected_title": window["title"],
        },
    )
    snapshot = result.get("desktop_snapshot")
    if not snapshot:
        raise RuntimeError("worker returned no desktop snapshot")
    return base64.b64decode(snapshot["png_base64"])


def _perform(
    settings: Settings,
    window: dict[str, Any],
    action: dict[str, Any],
    checkpoint: str,
) -> dict[str, Any]:
    payload = {
        "action": "desktop_input",
        "window_handle": window["handle"],
        "expected_pid": window["process_id"],
        "expected_title": window["title"],
        "checkpoint": checkpoint,
        "operation": action["action"],
        "apply": True,
    }
    if action.get("x") is not None:
        payload["x"] = int(action["x"])
    if action.get("y") is not None:
        payload["y"] = int(action["y"])
    if action.get("delta") is not None:
        payload["delta"] = int(action["delta"])
    if action.get("text") is not None:
        payload["text"] = str(action["text"])
    return _worker(settings, payload)


def _repair_json_candidate(candidate: str) -> str:
    """Fix common model JSON mistakes."""
    # Fix "x":123,456, -> "x":123,"y":456, (comma-separated coordinates)
    candidate = re.sub(
        r'"x"\s*:\s*(\d+)\s*,\s*(\d+)\s*,',
        r'"x":\1,"y":\2,',
        candidate,
    )
    # Fix missing y when x is present and followed by a bare number
    candidate = re.sub(
        r'"x"\s*:\s*(\d+)\s*,\s*(\d+)\s*}',
        r'"x":\1,"y":\2}',
        candidate,
    )
    return candidate


def _extract_json_action(content: str) -> dict[str, Any]:
    """Extract the first valid JSON action object from model output."""
    # Try the whole content first.
    candidates = [content]
    # Then try substrings between braces, preferring balanced blocks.
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        candidates.append(content[start : end + 1])
    # Scan for balanced JSON objects.
    depth = 0
    block_start: int | None = None
    for index, char in enumerate(content):
        if char == "{":
            if depth == 0:
                block_start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and block_start is not None:
                    candidates.append(content[block_start : index + 1])
                    block_start = None
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        for variant in (candidate, _repair_json_candidate(candidate)):
            try:
                action = json.loads(variant)
            except json.JSONDecodeError:
                continue
            if isinstance(action, dict) and "action" in action:
                return action
    raise RuntimeError(f"model did not return a valid JSON action: {content!r}")


def _model_action(
    settings: Settings,
    screenshot_png: bytes,
    *,
    task: str,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    system = (
        "You are a universal Windows desktop controller. Choose exactly one next action "
        "to accomplish the user's task. The desktop snapshot shows the current state of "
        "the pinned window. Return strict JSON only.\n\n"
        "Allowed actions and their JSON format:\n"
        '- click/right_click/double_click: {"action":"click","x":123,"y":456,'
        '"delta":null,"text":null,"reason":"..."}\n'
        '- wheel: {"action":"wheel","x":123,"y":456,"delta":1,"text":null,'
        '"reason":"..."}\n'
        '- type_text: {"action":"type_text","x":null,"y":null,"delta":null,'
        '"text":"hello","reason":"..."}\n'
        '- key_enter/key_escape/key_f2/key_ctrl_a: {"action":"key_enter","x":null,'
        '"y":null,"delta":null,"text":null,"reason":"..."}\n'
        '- wait: {"action":"wait","x":null,"y":null,"delta":null,"text":null,'
        '"reason":"..."}\n'
        '- done: {"action":"done","x":null,"y":null,"delta":null,"text":null,'
        '"reason":"..."}\n'
        '- failed: {"action":"failed","x":null,"y":null,"delta":null,"text":null,'
        '"reason":"..."}\n\n'
        "Never return an action outside this list. Coordinates are relative to the window image."
    )
    user_content = [
        {
            "type": "text",
            "text": (
                f"Task: {task}\n\n"
                f"Recent actions: {json.dumps(history[-6:], ensure_ascii=False)}\n\n"
                "Choose the next action."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{base64.b64encode(screenshot_png).decode()}"
            },
        },
    ]
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.0,
        "max_tokens": 512,
    }
    result = _json_request(
        f"{settings.llm_endpoint}/chat/completions",
        payload=payload,
        key=settings.llm_key,
        timeout=120,
    )
    content = result["choices"][0]["message"]["content"]
    action = _extract_json_action(content)
    if action.get("action") not in ACTION_SCHEMA["properties"]["action"]["enum"]:
        raise RuntimeError(f"model returned unsupported action: {action.get('action')!r}")
    if action["action"] in {"click", "right_click", "double_click", "wheel"} and (
        action.get("x") is None or action.get("y") is None
    ):
        raise RuntimeError("model returned coordinates action without x/y")
    if action["action"] == "wheel" and action.get("delta") is None:
        raise RuntimeError("model returned wheel without delta")
    if action["action"] == "type_text" and not action.get("text"):
        raise RuntimeError("model returned type_text without text")
    return action


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Universal vision-guided Windows desktop agent"
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Natural-language task for the agent to perform in the pinned window",
    )
    parser.add_argument(
        "--window-title-contains",
        required=True,
        help="Substring of the target window title",
    )
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--llm-env", default=".env")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--log-dir", default=".autocomp")
    args = parser.parse_args()

    project = Path(__file__).resolve().parent.parent
    settings = _settings(project / args.worker_env, project / args.llm_env)
    window = _select_window(settings, args.window_title_contains)

    log_dir = project / args.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"universal-run-{int(time.time())}.jsonl"

    with log_path.open("x", encoding="utf-8") as log:
        history: list[dict[str, Any]] = []
        for step in range(1, args.max_steps + 1):
            screenshot = _snapshot(settings, window)
            action: dict[str, Any] | None = None
            for attempt in range(3):
                try:
                    action = _model_action(
                        settings, screenshot, task=args.task, history=history
                    )
                    break
                except (json.JSONDecodeError, RuntimeError) as exc:
                    if attempt == 2:
                        raise
                    print(f"Model response invalid (attempt {attempt + 1}/3): {exc}")
                    time.sleep(0.5)
            if action is None:
                raise RuntimeError("model failed to produce a valid action")
            if len(history) >= 3 and all(
                previous.get("action") == action.get("action")
                and previous.get("x") == action.get("x")
                and previous.get("y") == action.get("y")
                for previous in history[-3:]
            ):
                print("Stopped repeated action loop; need a fresh strategy.")
                return 2

            event = {"step": step, "action": action}
            print(json.dumps(event, ensure_ascii=False), flush=True)

            if action["action"] == "done":
                event["result"] = "completed"
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                break
            if action["action"] == "failed":
                event["result"] = "model_failed"
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                break

            if action["action"] == "wait":
                time.sleep(1)
                worker_result = {"performed": True, "message": "wait"}
            else:
                worker_result = _perform(
                    settings, window, action, checkpoint=f"universal_{step:03d}"
                )
                time.sleep(0.3)

            event["worker"] = {
                "performed": worker_result.get("performed"),
                "message": worker_result.get("message"),
                "request_id": worker_result.get("request_id"),
            }
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
            log.flush()
            history.append(action)
        else:
            print(f"Reached max steps; see {log_path}")
            return 2

    print(f"Completed; log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
