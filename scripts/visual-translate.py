"""One-off vision-guided KV STUDIO translator.

The controller asks a local OpenAI-compatible vision model for one UI action at
a time and sends that action to the remote AUTOComp worker.  It is intentionally
small and optimized for the current project rather than for reuse as a general
desktop agent.
"""

from __future__ import annotations

import argparse
import json
import os
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


def _settings(project: Path, worker_env: Path, llm_env: Path) -> Settings:
    values = {**_dotenv(llm_env), **_dotenv(worker_env), **os.environ}
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
    del project
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
    key: str = "",
    bearer: str = "",
    timeout: float = 120,
) -> Any:
    headers = {"Accept": "application/json"}
    token = bearer or key
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


def _worker(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    result = _json_request(
        f"{settings.worker_endpoint}/v1/action",
        payload=payload,
        bearer=settings.worker_token,
        timeout=120,
    )
    if not isinstance(result, dict):
        raise RuntimeError("worker returned a non-object response")
    return result


def _desktop_windows(settings: Settings) -> list[dict[str, Any]]:
    result = _worker(settings, {"action": "desktop_windows"})
    windows = result.get("desktop_windows")
    if not isinstance(windows, list):
        raise RuntimeError(f"worker returned no desktop window list: {result}")
    return [item for item in windows if isinstance(item, dict)]


def _select_window(settings: Settings, title_fragment: str) -> dict[str, Any]:
    matches = [
        item
        for item in _desktop_windows(settings)
        if title_fragment.casefold() in str(item.get("title", "")).casefold()
        and not item.get("minimized")
    ]
    if len(matches) != 1:
        titles = [item.get("title", "") for item in matches]
        raise RuntimeError(
            f"expected one visible window containing {title_fragment!r}; found {titles}"
        )
    return matches[0]


def _snapshot(settings: Settings, window: dict[str, Any]) -> dict[str, Any]:
    result = _worker(
        settings,
        {
            "action": "desktop_snapshot",
            "window_handle": int(window["handle"]),
            "expected_pid": int(window["process_id"]),
            "expected_title": str(window["title"]),
        },
    )
    snapshot = result.get("desktop_snapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("png_base64"):
        raise RuntimeError(f"worker returned no screenshot: {result}")
    return snapshot


def _model_action(
    settings: Settings,
    snapshot: dict[str, Any],
    *,
    source: str,
    target: str,
    path: list[str],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt = f"""You control the visible Chinese KV STUDIO 11.62 editor.
This is a disposable offline copy of a PLC project. Work visually like a human.

Current single goal:
- exact tree hierarchy: {json.dumps(path, ensure_ascii=False)}
- replace exact Chinese/user text: {source!r}
- with technical English: {target!r}

The screenshot is {snapshot['width']}x{snapshot['height']}. Coordinates are relative to it.
Choose exactly ONE next action. Inspect the image carefully. You may expand tree nodes,
scroll the project tree, open the ladder/program, use Chinese context menus or dialogs,
select an edit field, type the supplied target, and confirm. Do not touch PLC transfer,
monitor, run, simulator, online, download, upload, or communication commands.
If the exact target text is visibly committed, return done. If waiting for UI, return wait.
Never invent coordinates outside the screenshot. For wheel use delta -6 to scroll down or
+6 to scroll up at a point inside the project tree. For type_text, return exactly the target
text when an editor is focused. Keep reason short.

Recent actions: {json.dumps(history[-8:], ensure_ascii=False)}
"""
    body = {
        "model": settings.llm_model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{snapshot['png_base64']}"
                        },
                    },
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "kv_visual_action",
                "strict": True,
                "schema": ACTION_SCHEMA,
            },
        },
    }
    completion = _json_request(
        f"{settings.llm_endpoint}/chat/completions",
        payload=body,
        key=settings.llm_key,
        timeout=180,
    )
    try:
        content = completion["choices"][0]["message"]["content"]
        action = json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid vision model response: {completion}") from exc
    if not isinstance(action, dict) or action.get("action") not in ACTION_SCHEMA["properties"][
        "action"
    ]["enum"]:
        raise RuntimeError(f"invalid visual action: {action}")
    return action


def _perform(
    settings: Settings,
    window: dict[str, Any],
    action: dict[str, Any],
    checkpoint: str,
) -> dict[str, Any]:
    operation = str(action["action"])
    operation = {
        "right_click": "right",
        "double_click": "double",
    }.get(operation, operation)
    payload: dict[str, Any] = {
        "action": "desktop_input",
        "window_handle": int(window["handle"]),
        "expected_pid": int(window["process_id"]),
        "expected_title": str(window["title"]),
        "checkpoint": checkpoint,
        "operation": operation,
        "apply": True,
    }
    if operation in {"click", "right", "double", "wheel"}:
        payload["x"] = int(action["x"])
        payload["y"] = int(action["y"])
    if operation == "wheel":
        payload["delta"] = max(-12, min(12, int(action["delta"])))
    if operation == "type_text":
        payload["text"] = str(action["text"])
    return _worker(settings, payload)


def _targets(project: Path) -> list[dict[str, Any]]:
    records = json.loads(
        (project / "reports/02-tree-translation-inventory.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (project / "reports/02-tree-translation-manifest.json").read_text(encoding="utf-8")
    )
    by_id = {item["record_id"]: item for item in records}
    targets: list[dict[str, Any]] = []
    for decision in manifest["decisions"]:
        record = by_id[decision["record_id"]]
        targets.append(
            {
                "record_id": decision["record_id"],
                "source": decision["source_text"],
                "target": decision["target_text"],
                "path": [part for part in record["hierarchy"] if not part.startswith("locator:")],
                "kind": record["kind"],
            }
        )
    # Bookmark labels first; program names last so parent paths remain stable.
    return sorted(targets, key=lambda item: item["kind"] == "program_name")


def main() -> int:
    parser = argparse.ArgumentParser(description="One-off visual KV STUDIO translator")
    parser.add_argument("--worker-env", default=".env.remote")
    parser.add_argument("--llm-env", default=".env")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--start-record", default="")
    parser.add_argument("--window-title-contains", default="KV STUDIO - [")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("This one-off controller requires explicit --apply")
    project = Path(__file__).resolve().parent.parent
    settings = _settings(project, project / args.worker_env, project / args.llm_env)
    window = _select_window(settings, args.window_title_contains)
    targets = _targets(project)
    if args.start_record:
        start = next(
            (index for index, item in enumerate(targets) if item["record_id"] == args.start_record),
            None,
        )
        if start is None:
            raise SystemExit("start record not found")
        targets = targets[start:]
    targets = targets[: max(1, args.limit)]
    log_path = project / ".autocomp" / f"visual-run-{int(time.time())}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("x", encoding="utf-8") as log:
        for target_index, target in enumerate(targets, 1):
            history: list[dict[str, Any]] = []
            completed = False
            for step in range(1, args.max_steps + 1):
                snapshot = _snapshot(settings, window)
                action = _model_action(settings, snapshot, history=history, **{
                    key: target[key] for key in ("source", "target", "path")
                })
                event = {
                    "target_index": target_index,
                    "record_id": target["record_id"],
                    "step": step,
                    "action": action,
                }
                print(json.dumps(event, ensure_ascii=False), flush=True)
                if action["action"] == "done":
                    completed = True
                    event["result"] = "completed_by_visual_confirmation"
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                    log.flush()
                    break
                if action["action"] == "failed":
                    event["result"] = "model_failed"
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                    log.flush()
                    break
                if action["action"] == "wait":
                    time.sleep(1)
                    worker_result: dict[str, Any] = {"waited": True}
                else:
                    worker_result = _perform(
                        settings,
                        window,
                        action,
                        checkpoint=f"visual_{target_index:03d}_{step:03d}",
                    )
                    time.sleep(0.35)
                event["worker"] = {
                    "performed": worker_result.get("performed"),
                    "message": worker_result.get("message"),
                    "request_id": worker_result.get("request_id"),
                }
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                log.flush()
                history.append(action)
            if not completed:
                print(f"Stopped at {target['record_id']}; see {log_path}", flush=True)
                return 2
    print(f"Completed {len(targets)} target(s); log: {log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
