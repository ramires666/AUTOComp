from __future__ import annotations

import argparse
import copy
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "visual-translate.py"


@pytest.fixture(scope="module")
def visual() -> dict[str, Any]:
    return runpy.run_path(str(SCRIPT))


def _approved_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": "approved_ui_rename_manifest",
        "checkpoint": "03-bookmarks-approved",
        "apply_gate": {
            "apply_enabled": True,
            "requires_explicit_apply_flag": True,
            "requires_named_checkpoint": True,
            "program_names_excluded": True,
        },
        "items": [
            {
                "record_id": "record-1",
                "locator": [4, 0, 0, 1, 7],
                "expected_path": ["Program", "Scan", "Init", "书签"],
                "expected_source": "/*报警*/",
                "target": "/*Alarm*/",
                "risk": "low",
                "requires_review": False,
            }
        ],
    }


def _patch_manifest_read(
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, Any],
) -> None:
    def read_text(path: Path, *, encoding: str) -> str:
        assert path.name == "03-approved-ui-rename-manifest.json"
        assert encoding == "utf-8"
        return json.dumps(manifest, ensure_ascii=False)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_targets_load_only_approved_items_and_preserve_preconditions(
    visual: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_manifest_read(monkeypatch, _approved_manifest())

    targets = visual["_targets"](Path("project"))

    assert targets == [
        {
            "record_id": "record-1",
            "locator": [4, 0, 0, 1, 7],
            "source": "/*报警*/",
            "target": "/*Alarm*/",
            "path": ["Program", "Scan", "Init", "书签"],
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("apply_enabled", False),
        ("requires_explicit_apply_flag", False),
        ("requires_named_checkpoint", False),
        ("program_names_excluded", False),
    ],
)
def test_targets_reject_invalid_apply_gate(
    visual: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: bool,
) -> None:
    manifest = _approved_manifest()
    manifest["apply_gate"][field] = value
    _patch_manifest_read(monkeypatch, manifest)

    with pytest.raises(ValueError, match="apply_gate"):
        visual["_targets"](Path("project"))


def test_targets_reject_wrong_artifact_type(
    visual: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _approved_manifest()
    manifest["artifact_type"] = "translation_manifest"
    _patch_manifest_read(monkeypatch, manifest)

    with pytest.raises(ValueError, match="artifact_type"):
        visual["_targets"](Path("project"))


@pytest.mark.parametrize(
    "item_update",
    [
        {"requires_review": True},
        {"kind": "program_name"},
        {"expected_path": ["Program", "Scan", "Program Name"]},
    ],
)
def test_targets_reject_review_required_and_program_name_items(
    visual: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    item_update: dict[str, Any],
) -> None:
    manifest = copy.deepcopy(_approved_manifest())
    manifest["items"][0].update(item_update)
    _patch_manifest_read(monkeypatch, manifest)

    with pytest.raises(ValueError, match="requires review|not a bookmark label"):
        visual["_targets"](Path("project"))


def _model_completion(text: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": {
                        "action": "type_text",
                        "x": None,
                        "y": None,
                        "delta": None,
                        "text": text,
                        "reason": "Replace text",
                    }
                }
            }
        ]
    }


def _call_model_action(
    visual: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    returned_text: str,
) -> dict[str, Any]:
    model_action = visual["_model_action"]
    monkeypatch.setitem(
        model_action.__globals__,
        "_json_request",
        lambda *args, **kwargs: _model_completion(returned_text),
    )
    settings = visual["Settings"]("worker", "token", "http://llm/v1", "", "model")
    return model_action(
        settings,
        {"width": 100, "height": 80, "png_base64": "iVBORw0KGgo="},
        source="/*报警*/",
        target="/*Alarm*/",
        path=["Program", "书签"],
        locator=[4, 0, 7],
        history=[],
    )


def test_model_type_text_must_exactly_match_approved_target(
    visual: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RuntimeError, match="exactly equal"):
        _call_model_action(visual, monkeypatch, "/*Different*/")


def test_model_type_text_accepts_exact_approved_target(
    visual: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    action = _call_model_action(visual, monkeypatch, "/*Alarm*/")

    assert action["text"] == "/*Alarm*/"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_limit_must_be_positive(visual: dict[str, Any], value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="positive integer"):
        visual["_positive_limit"](value)
