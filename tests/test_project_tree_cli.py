from __future__ import annotations

import json

import pytest

import autocomp.cli as cli
from autocomp.worker.models import ProjectTreeInventory, ProjectTreeNodeSnapshot


def _forbid_adapter_creation(*args: object, **kwargs: object) -> object:
    del args, kwargs
    raise AssertionError("adapter must not be created before CLI safety validation")


@pytest.mark.parametrize(
    "arguments, expected_error",
    [
        (["--expand-all", "--checkpoint", "01-tree"], "requires explicit --apply"),
        (["--expand-all", "--apply"], "requires a non-empty --checkpoint"),
        (
            ["--expand-all", "--apply", "--checkpoint", "   "],
            "requires a non-empty --checkpoint",
        ),
    ],
)
def test_expand_all_requires_apply_and_non_empty_checkpoint(
    tmp_path, monkeypatch, capsys, arguments, expected_error
) -> None:
    monkeypatch.setattr(cli, "PywinautoKVStudioAdapter", _forbid_adapter_creation)
    output = tmp_path / "tree.json"

    with pytest.raises(SystemExit) as error:
        cli.main(["inventory-project-tree", "--output", str(output), *arguments])

    assert error.value.code == 2
    assert expected_error in capsys.readouterr().err
    assert not output.exists()


def test_existing_output_is_rejected_before_adapter_creation(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "PywinautoKVStudioAdapter", _forbid_adapter_creation)
    output = tmp_path / "tree.json"
    output.write_text("keep this report", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "inventory-project-tree",
                "--output",
                str(output),
                "--expand-all",
                "--apply",
                "--checkpoint",
                "01-tree",
            ]
        )

    assert error.value.code == 2
    assert "refusing to overwrite existing output" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "keep this report"


def test_success_writes_nested_json_and_passes_expansion_restore_and_limits(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    inventory = ProjectTreeInventory(
        window_title="Example - KV STUDIO",
        process_id=101,
        automation_id="ProjectTreeView",
        item_count=2,
        expanded_count=1,
        restored_count=1,
        restore_requested=True,
        roots=(
            ProjectTreeNodeSnapshot(
                name="Programs",
                path=("Programs",),
                depth=0,
                sibling_index=0,
                locator=(0,),
                initial_expansion_state="collapsed",
                expanded_for_inventory=True,
                visible=True,
                children=(
                    ProjectTreeNodeSnapshot(
                        name="PartsLife",
                        path=("Programs", "PartsLife"),
                        depth=1,
                        sibling_index=0,
                        locator=(0, 0),
                        initial_expansion_state="leaf",
                        visible=True,
                    ),
                ),
            ),
        ),
    )

    class _FakeProjectTreeAdapter:
        def __init__(self, title_pattern: str, **limits: object) -> None:
            captured["title_pattern"] = title_pattern
            captured["limits"] = limits

        def inventory_project_tree(
            self, *, expand_all: bool, restore_state: bool
        ) -> ProjectTreeInventory:
            captured["inventory_call"] = {
                "expand_all": expand_all,
                "restore_state": restore_state,
            }
            return inventory

    monkeypatch.setattr(cli, "PywinautoKVStudioAdapter", _FakeProjectTreeAdapter)
    output = tmp_path / "reports" / "tree.json"

    exit_code = cli.main(
        [
            "inventory-project-tree",
            "--output",
            str(output),
            "--expand-all",
            "--apply",
            "--checkpoint",
            "01-tree",
            "--max-depth",
            "7",
            "--max-items",
            "123",
            "--max-expansions",
            "45",
            "--timeout-seconds",
            "6.5",
        ]
    )

    assert exit_code == 0
    assert captured["inventory_call"] == {"expand_all": True, "restore_state": True}
    assert captured["limits"] == {
        "max_project_depth": 7,
        "max_project_items": 123,
        "max_project_expansions": 45,
        "max_project_seconds": 6.5,
    }
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["checkpoint"] == "01-tree"
    assert payload["mode"] == "apply"
    assert payload["requested"] == {"expand_all": True, "restore_state": True}
    assert payload["inventory"]["roots"][0]["name"] == "Programs"
    assert payload["inventory"]["roots"][0]["children"][0]["name"] == "PartsLife"
    assert payload["inventory"]["roots"][0]["children"][0]["path"] == [
        "Programs",
        "PartsLife",
    ]
    assert payload["audit"] == {
        "operation": "inventory_project_tree",
        "ui_mutation": "expand_collapse_only",
        "project_content_changed": False,
        "plc_operations": "forbidden",
    }
