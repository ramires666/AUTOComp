from __future__ import annotations

import json

import pytest

from autocomp.config import ConfigError, load_config


def test_safe_defaults_are_dry_run() -> None:
    config = load_config()
    assert config.safety.apply_enabled is False
    assert config.safety.forbid_online_operations is True
    assert config.kv_studio.expected_version == "11.62"


def test_rejects_enabling_online_operations(tmp_path) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps({"safety": {"forbid_online_operations": False}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="online PLC operations"):
        load_config(path)


def test_rejects_non_http_endpoint(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"llm": {"endpoint": "file:///secret"}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="HTTP"):
        load_config(path)
