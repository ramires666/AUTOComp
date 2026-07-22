from __future__ import annotations

import base64
import hashlib
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "universal-vision-agent.py"


@pytest.fixture(scope="module")
def agent() -> dict[str, Any]:
    return runpy.run_path(str(SCRIPT))


def _window() -> dict[str, Any]:
    return {
        "handle": 101,
        "title": "Any application",
        "process_id": 202,
        "bounds": [0, 0, 800, 600],
        "minimized": False,
    }


def _decision(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": "input",
        "window_index": None,
        "operations": [
            {
                "operation": "click",
                "x": 10,
                "y": 20,
                "delta": None,
                "text": None,
                "pause_ms": 100,
            }
        ],
        "wait_seconds": None,
        "reason": "click visible control",
        "evidence": "control is visible",
    }
    value.update(changes)
    return value


def test_decision_schema_rejects_coordinates_outside_current_frame(
    agent: dict[str, Any],
) -> None:
    value = _decision()
    value["operations"][0]["x"] = 1001  # type: ignore[index]

    with pytest.raises(ValueError, match="outside 0..1000"):
        agent["_validate_decision"](
            value,
            windows=[_window()],
            frame={"width": 800, "height": 600},
        )


def test_select_window_is_pinned_to_current_enumeration(agent: dict[str, Any]) -> None:
    value = _decision(
        kind="select_window",
        window_index=0,
        operations=[],
    )

    result = agent["_validate_decision"](value, windows=[_window()], frame=None)

    assert result["window_index"] == 0


def test_atomic_input_payload_contains_only_worker_protocol_fields(
    agent: dict[str, Any],
) -> None:
    operations = _decision()["operations"]

    payload = agent["_input_payload"](
        window=_window(),
        frame={"width": 800, "height": 600},
        operations=operations,
        checkpoint="vision-abc-00001",
    )

    assert payload == {
        "action": "desktop_input_sequence",
        "window_handle": 101,
        "expected_pid": 202,
        "expected_title": "Any application",
        "checkpoint": "vision-abc-00001",
        "operations": [
            {"operation": "click", "pause_ms": 100, "x": 8, "y": 12}
        ],
        "apply": True,
    }


@pytest.mark.parametrize(
    "operation",
    ["key_ctrl_c", "key_ctrl_d", "key_ctrl_home", "key_ctrl_shift_end"],
)
def test_fixed_selection_and_copy_shortcuts_are_in_runner_schema(
    agent: dict[str, Any], operation: str
) -> None:
    step = {
        "operation": operation,
        "x": None,
        "y": None,
        "delta": None,
        "text": None,
        "pause_ms": 100,
    }

    agent["_validate_decision"](
        _decision(operations=[step]),
        windows=[_window()],
        frame={"width": 800, "height": 600},
    )
    assert operation in agent["DECISION_SCHEMA"]["properties"]["operations"]["items"][
        "properties"
    ]["operation"]["enum"]


def test_state_preserves_mission_and_full_translation_text(
    agent: dict[str, Any], tmp_path: Path
) -> None:
    state_path = tmp_path / "mission.json"
    mission = {
        "schema_version": 1,
        "objective": "Translate",
        "context": "Generic editor",
        "goals": [
            {
                "id": "translate-1",
                "objective": "Replace 中文 with Precise English",
                "context": "Visible text field",
                "success_criteria": ["Precise English is visibly committed"],
                "allowed_text": [
                    {"original": "中文", "english": "Precise English", "russian": None}
                ],
            }
        ],
    }
    state = agent["_load_state"](state_path, mission)
    event = {
        "step": 1,
        "event": "decision",
        "decision": _decision(
            operations=[
                {
                    "operation": "type_text",
                    "x": None,
                    "y": None,
                    "delta": None,
                    "text": "Precise English",
                    "pause_ms": 100,
                }
            ]
        ),
    }

    agent["_append_event"](state, event, state_path)
    saved = json.loads(state_path.read_text(encoding="utf-8"))

    assert saved["mission"] == mission
    assert saved["events"][0]["decision"]["operations"][0]["text"] == "Precise English"


def test_mission_allows_empty_source_field_for_new_split_comment(
    agent: dict[str, Any],
) -> None:
    mission = {
        "schema_version": 1,
        "objective": "Split a label into name and comment fields",
        "context": "Generic editor",
        "goals": [
            {
                "id": "split-1",
                "objective": "Move an existing description into a new comment",
                "context": "The original comment field is empty",
                "success_criteria": ["Both fields are visibly correct"],
                "allowed_text": [
                    {
                        "original": "原名称（描述）",
                        "english": "SafeName:Description",
                        "russian": None,
                    },
                    {"original": "原名称（描述）", "english": "SafeName", "russian": None},
                    {"original": "", "english": "Description", "russian": None},
                ],
            }
        ],
    }

    assert agent["_validate_mission"](mission) == mission


def test_active_goal_rejects_unapproved_typed_text(agent: dict[str, Any]) -> None:
    value = _decision(
        operations=[
            {
                "operation": "type_text",
                "x": None,
                "y": None,
                "delta": None,
                "text": "Invented text",
                "pause_ms": 100,
            }
        ]
    )

    with pytest.raises(ValueError, match="not allowlisted"):
        agent["_validate_decision"](
            value,
            windows=[_window()],
            frame={"width": 800, "height": 600},
            allowed_type_text={"中文", "Precise English"},
        )


def test_sequence_cannot_cross_unverified_ui_transitions(agent: dict[str, Any]) -> None:
    value = _decision(
        operations=[
            {
                "operation": "click",
                "x": 10,
                "y": 20,
                "delta": None,
                "text": None,
                "pause_ms": 100,
            },
            {
                "operation": "key_f2",
                "x": None,
                "y": None,
                "delta": None,
                "text": None,
                "pause_ms": 100,
            },
            {
                "operation": "type_text",
                "x": None,
                "y": None,
                "delta": None,
                "text": "Precise English",
                "pause_ms": 100,
            },
        ]
    )

    with pytest.raises(ValueError, match="fresh frame"):
        agent["_validate_decision"](
            value,
            windows=[_window()],
            frame={"width": 800, "height": 600},
            allowed_type_text={"Precise English"},
        )


def test_focused_field_can_be_replaced_without_another_click(
    agent: dict[str, Any],
) -> None:
    value = _decision(
        operations=[
            {
                "operation": "key_ctrl_a",
                "x": None,
                "y": None,
                "delta": None,
                "text": None,
                "pause_ms": 100,
            },
            {
                "operation": "type_text",
                "x": None,
                "y": None,
                "delta": None,
                "text": "Precise English",
                "pause_ms": 100,
            },
        ]
    )

    agent["_validate_decision"](
        value,
        windows=[_window()],
        frame={"width": 800, "height": 600},
        allowed_type_text={"Precise English"},
    )


def test_verified_six_tab_field_route_can_end_in_atomic_replacement(
    agent: dict[str, Any],
) -> None:
    operations = [
        {
            "operation": operation,
            "x": None,
            "y": None,
            "delta": None,
            "text": "Precise English" if operation == "type_text" else None,
            "pause_ms": 100,
        }
        for operation in (*("tab",) * 6, "key_ctrl_a", "type_text")
    ]

    agent["_validate_decision"](
        _decision(operations=operations),
        windows=[_window()],
        frame={"width": 800, "height": 600},
        allowed_type_text={"Precise English"},
    )


def test_worker_handshake_records_exact_remote_build(
    agent: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = {
        "http://worker/health": {"status": "ok", "build_id": "build-123"},
        "http://worker/v1/capabilities": {
            "build_id": "build-123",
            "boot_id": "boot-456",
            "started_at": "2026-07-21T00:00:00Z",
            "actions": [
                "desktop_windows",
                "desktop_snapshot",
                "desktop_input_sequence",
            ],
            "operation_limits": {"sequence_max_operations": 8},
        },
    }

    def fake_request(url: str, **_: object) -> object:
        return responses[url]

    monkeypatch.setitem(agent["_worker_handshake"].__globals__, "_request_json", fake_request)
    settings = agent["Settings"]("http://worker", "token", "http://llm", "", "model")

    assert agent["_worker_handshake"](settings) == {
        "build_id": "build-123",
        "boot_id": "boot-456",
        "started_at": "2026-07-21T00:00:00Z",
        "operation_limits": {"sequence_max_operations": 8},
    }


def test_frame_evidence_is_hash_verified_before_save(
    agent: dict[str, Any], tmp_path: Path
) -> None:
    png = b"a small deterministic png placeholder"
    digest = hashlib.sha256(png).hexdigest()
    frame = {
        "png_base64": base64.b64encode(png).decode("ascii"),
        "png_sha256": digest,
    }

    saved = Path(agent["_save_frame"](tmp_path / "state.json", 7, frame))

    assert saved.read_bytes() == png
    assert saved.name == f"00007-{digest[:12]}.png"

    frame["png_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="hash does not match"):
        agent["_save_frame"](tmp_path / "state.json", 8, frame)


def test_model_uses_bounded_output_and_falls_back_after_grammar_rejection(
    agent: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, Any]] = []

    def fake_request(url: str, *, payload: dict[str, Any], **_: object) -> object:
        assert url.endswith("/chat/completions")
        requests.append(payload.copy())
        if "response_format" in payload:
            raise RuntimeError("HTTP 500: Failed to initialize samplers: grammar")
        return {"choices": [{"message": {"content": json.dumps(_decision())}}]}

    monkeypatch.setitem(agent["_model_decision"].__globals__, "_request_json", fake_request)
    settings = agent["Settings"]("http://worker", "token", "http://llm", "", "model")

    result = agent["_model_decision"](
        settings,
        prompt="observe",
        frame={"width": 800, "height": 600, "png_base64": ""},
        windows=[_window()],
        allowed_type_text=None,
        system_prompt="return JSON",
    )

    assert result["kind"] == "input"
    assert requests[0]["max_tokens"] == 512
    assert "response_format" in requests[0]
    assert "response_format" not in requests[1]


def test_foreground_owned_popup_is_routed_before_visual_input(
    agent: dict[str, Any],
) -> None:
    main = {**_window(), "enabled": False, "foreground": False, "owner_handle": 0}
    popup = {
        **_window(),
        "handle": 303,
        "title": "Modal",
        "enabled": True,
        "foreground": True,
        "owner_handle": 101,
    }

    assert agent["_foreground_route"](main, [main, popup]) == popup

    popup["foreground"] = False
    assert agent["_foreground_route"](main, [main, popup]) == popup


def test_identical_frame_and_input_has_a_stable_repeat_signature(
    agent: dict[str, Any],
) -> None:
    frame = {"png_sha256": "a" * 64}
    operations = _decision()["operations"]
    signature = agent["_input_signature"](
        frame=frame, window=_window(), operations=operations
    )
    state = {
        "events": [
            {
                "event": "decision",
                "phase": "intent",
                "input_signature": signature,
            }
        ]
    }

    assert agent["_input_attempt_count"](state, signature) == 1
