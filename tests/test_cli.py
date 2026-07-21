from __future__ import annotations

import json

import pytest

from autocomp.cli import CliError, _emit, _load_inventory, _parser, main
from autocomp.translation.models import InventoryRecord, RiskLevel, TextKind


def test_doctor_reports_safe_defaults(capsys) -> None:
    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["online_operations_forbidden"] is True
    assert report["expected_kv_studio_version"] == "11.62"
    assert "api_key" not in report


def test_doctor_reports_dotenv_without_exposing_secret(tmp_path, capsys, monkeypatch) -> None:
    for name in (
        "AUTOCOMP_LLM_ENDPOINT",
        "AUTOCOMP_LLM_MODEL",
        "AUTOCOMP_LLM_API_KEY",
        "AUTOCOMP_WORKER_TOKEN",
        "AUTOCOMP_WORKER_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "AUTOCOMP_LLM_ENDPOINT=http://127.0.0.1:8080/v1\n"
        "AUTOCOMP_LLM_MODEL=qwen-test\n"
        "AUTOCOMP_LLM_API_KEY=never-print-this\n",
        encoding="utf-8",
    )

    assert main(["doctor", "--env-file", str(env_path)]) == 0
    rendered = capsys.readouterr().out
    report = json.loads(rendered)
    assert report["llm_endpoint"] == "http://127.0.0.1:8080/v1"
    assert report["llm_model"] == "qwen-test"
    assert report["llm_api_key_configured"] is True
    assert "never-print-this" not in rendered


def test_program_name_inventory_is_high_risk(tmp_path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            [
                {
                    "record_id": "program:1",
                    "source_text": "通信程序",
                    "kind": "program_name",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    records = _load_inventory(inventory)

    assert records[0].risk is RiskLevel.HIGH
    assert records[0].requires_review is True


def test_loader_preserves_extractor_review_escalation(tmp_path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            [
                {
                    "record_id": "fallback:1",
                    "source_text": "未知格式",
                    "kind": "other",
                    "risk": "medium",
                    "requires_review": True,
                    "hierarchy": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    record = _load_inventory(inventory)[0]
    assert record.risk is RiskLevel.MEDIUM
    assert record.requires_review is True


def test_output_is_never_overwritten_implicitly(tmp_path) -> None:
    output = tmp_path / "report.json"
    _emit({"first": True}, str(output))

    with pytest.raises(CliError, match="refusing to overwrite"):
        _emit({"second": True}, str(output))


def test_compare_mnemonic_command_detects_constant_change(tmp_path) -> None:
    baseline = tmp_path / "before.txt"
    candidate = tmp_path / "after.txt"
    baseline.write_text("MOV #90 DM530\n", encoding="utf-8")
    candidate.write_text("MOV #100 DM530\n", encoding="utf-8")

    assert main(["compare-mnemonic", str(baseline), str(candidate)]) == 1


def test_extract_mnemonic_command_writes_inventory(tmp_path) -> None:
    source = tmp_path / "PartsLife.txt"
    output = tmp_path / "inventory.json"
    source.write_text("MOV #90 DM530 ; 寿命设置\n", encoding="utf-8")

    assert main(["extract-mnemonic", str(source), "--output", str(output)]) == 0
    inventory = json.loads(output.read_text(encoding="utf-8"))
    assert inventory[0]["source_text"] == "寿命设置"
    assert inventory[0]["location"] == "PartsLife.txt:1"


def test_extract_project_tree_command_writes_inventory(tmp_path, monkeypatch) -> None:
    source = tmp_path / "tree.json"
    output = tmp_path / "inventory.json"
    source.write_text('{"source":true}', encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_extract(payload: object, *, source_name: str):
        captured.update(payload=payload, source_name=source_name)
        return [InventoryRecord("tree:1", "报警", TextKind.COMMENT)]

    monkeypatch.setattr("autocomp.cli.extract_project_tree_inventory", fake_extract)

    assert main(["extract-project-tree", str(source), "--output", str(output)]) == 0
    inventory = json.loads(output.read_text(encoding="utf-8"))
    assert captured == {"payload": {"source": True}, "source_name": "tree.json"}
    assert inventory[0]["source_text"] == "报警"


def test_worker_serve_requires_explicit_audit_destination() -> None:
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["worker-serve"])

    args = parser.parse_args(
        [
            "worker-serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--audit-log",
            ".autocomp/worker-audit.jsonl",
        ]
    )
    assert args.host == "127.0.0.1"
    assert args.audit_log.endswith("worker-audit.jsonl")
    assert args.allow_remote is False
    assert args.enable_kv_studio_adapter is False

    kv_accelerated = parser.parse_args(
        [
            "worker-serve",
            "--audit-log",
            ".autocomp/worker-audit.jsonl",
            "--enable-kv-studio-adapter",
        ]
    )
    assert kv_accelerated.enable_kv_studio_adapter is True
