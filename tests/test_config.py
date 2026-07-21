from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from autocomp.config import ConfigError, load_config

_ENV_NAMES = (
    "AUTOCOMP_LLM_ENDPOINT",
    "AUTOCOMP_LLM_MODEL",
    "AUTOCOMP_LLM_API_KEY",
    "AUTOCOMP_WORKER_TOKEN",
)


def _clear_autocomp_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_safe_defaults_are_dry_run() -> None:
    config = load_config()
    assert config.safety.apply_enabled is False
    assert config.safety.forbid_online_operations is True
    assert config.kv_studio.expected_version == "11.62"
    assert config.translation.target_language == "English"


def test_translation_project_context_is_loaded_and_validated(tmp_path) -> None:
    path = tmp_path / "context.json"
    path.write_text(
        json.dumps(
            {
                "translation": {
                    "target_language": "English",
                    "project_context": "Robotic gold-assay kiosk with an induction furnace.",
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert "gold-assay kiosk" in config.translation.project_context


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


def test_sibling_dotenv_overrides_json_and_loads_secrets(tmp_path, monkeypatch) -> None:
    _clear_autocomp_environment(monkeypatch)
    config_path = tmp_path / "config.local.json"
    config_path.write_text(
        json.dumps({"llm": {"endpoint": "http://127.0.0.1:8000/v1", "model": "old"}}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\ufeff# local only\n"
        "AUTOCOMP_LLM_ENDPOINT='http://127.0.0.1:8080/v1'\n"
        'AUTOCOMP_LLM_MODEL="qwen-local"\n'
        "AUTOCOMP_LLM_API_KEY=private-test-key\n"
        "AUTOCOMP_WORKER_TOKEN=0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.llm.endpoint == "http://127.0.0.1:8080/v1"
    assert config.llm.model == "qwen-local"
    assert config.llm.api_key == "private-test-key"
    assert config.worker_token == "0123456789abcdef0123456789abcdef"
    assert "private-test-key" not in repr(config)
    assert "0123456789abcdef0123456789abcdef" not in repr(config)
    serialized_representation = repr(asdict(config))
    assert "private-test-key" not in serialized_representation
    assert "0123456789abcdef0123456789abcdef" not in serialized_representation


def test_process_environment_overrides_dotenv(tmp_path, monkeypatch) -> None:
    _clear_autocomp_environment(monkeypatch)
    (tmp_path / ".env").write_text(
        "AUTOCOMP_LLM_ENDPOINT=http://127.0.0.1:8080/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOCOMP_LLM_ENDPOINT", "http://127.0.0.1:9090/v1")

    config = load_config(env_path=tmp_path / ".env")

    assert config.llm.endpoint == "http://127.0.0.1:9090/v1"


def test_explicit_missing_env_file_is_rejected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="file does not exist"):
        load_config(env_path=tmp_path / "missing.env")


def test_inline_api_key_is_rejected(tmp_path) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps({"llm": {"api_key": "must-not-be-here"}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="must be stored in .env"):
        load_config(path)


def test_dotenv_rejects_unknown_and_duplicate_names_without_values(tmp_path) -> None:
    secret = "do-not-echo-this-value"
    env_path = tmp_path / ".env"
    env_path.write_text(f"UNSUPPORTED_SECRET={secret}\n", encoding="utf-8")

    with pytest.raises(ConfigError) as error:
        load_config(env_path=env_path)
    assert secret not in str(error.value)

    env_path.write_text(
        "AUTOCOMP_LLM_MODEL=first\nAUTOCOMP_LLM_MODEL=second\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate variable AUTOCOMP_LLM_MODEL"):
        load_config(env_path=env_path)


def test_dotenv_endpoint_is_validated(tmp_path, monkeypatch) -> None:
    _clear_autocomp_environment(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text("AUTOCOMP_LLM_ENDPOINT=file:///secret\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="HTTP"):
        load_config(env_path=env_path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://user:password@127.0.0.1:8080/v1",
        "http://127.0.0.1:8080/v1?token=secret",
        "http://127.0.0.1:8080/v1#secret",
    ],
)
def test_endpoint_rejects_embedded_credentials(endpoint, tmp_path, monkeypatch) -> None:
    _clear_autocomp_environment(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(f"AUTOCOMP_LLM_ENDPOINT={endpoint}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="must not contain"):
        load_config(env_path=env_path)


def test_api_key_environment_name_cannot_be_repurposed(tmp_path) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(
        json.dumps({"llm": {"api_key_env": "AUTOCOMP_WORKER_TOKEN"}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="may only be AUTOCOMP_LLM_API_KEY"):
        load_config(path)


def test_process_secret_rejects_control_characters(monkeypatch) -> None:
    _clear_autocomp_environment(monkeypatch)
    monkeypatch.setenv("AUTOCOMP_LLM_API_KEY", "secret\nheader")

    with pytest.raises(ConfigError, match="must not contain control"):
        load_config()


def test_dotenv_rejects_control_character_before_splitting(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_bytes(b"AUTOCOMP_LLM_MODEL=safe\x0bhidden\n")

    with pytest.raises(ConfigError, match="unsupported control"):
        load_config(env_path=env_path)
