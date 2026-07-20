"""Runtime configuration with secrets supplied through environment variables."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_LLM_API_KEY_ENV = "AUTOCOMP_LLM_API_KEY"
LLM_ENDPOINT_ENV = "AUTOCOMP_LLM_ENDPOINT"
LLM_MODEL_ENV = "AUTOCOMP_LLM_MODEL"
WORKER_TOKEN_ENV = "AUTOCOMP_WORKER_TOKEN"
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_MAX_ENV_FILE_BYTES = 64 * 1024


class ConfigError(ValueError):
    """Raised when a configuration file is invalid or unsafe."""


class _SecretValues:
    """Keep secrets out of dataclass serialization and diagnostics."""

    __slots__ = ("_llm_api_key", "_worker_token")

    def __init__(self, llm_api_key: str | None, worker_token: str | None) -> None:
        self._llm_api_key = llm_api_key
        self._worker_token = worker_token

    @property
    def llm_api_key(self) -> str | None:
        return self._llm_api_key

    @property
    def worker_token(self) -> str | None:
        return self._worker_token

    def __repr__(self) -> str:
        return "<redacted secrets>"


@dataclass(frozen=True, slots=True)
class LlmConfig:
    endpoint: str = "http://127.0.0.1:8000/v1"
    model: str = "auto"
    timeout_seconds: float = 120.0
    _secrets: _SecretValues = field(
        default_factory=lambda: _SecretValues(None, None),
        repr=False,
        compare=False,
    )

    def validate(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("llm.endpoint must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigError("llm.endpoint must not contain credentials, query, or fragment")
        if not self.model.strip():
            raise ConfigError("llm.model must not be empty")
        if not 1 <= self.timeout_seconds <= 600:
            raise ConfigError("llm.timeout_seconds must be between 1 and 600")

    @property
    def api_key(self) -> str | None:
        return self._secrets.llm_api_key


@dataclass(frozen=True, slots=True)
class KvStudioConfig:
    window_title_pattern: str = r"\bKV STUDIO\b"
    preferred_backend: str = "uia"
    expected_version: str = "11.62"

    def validate(self) -> None:
        if self.preferred_backend not in {"uia", "win32"}:
            raise ConfigError("kv_studio.preferred_backend must be 'uia' or 'win32'")
        if not self.window_title_pattern.strip():
            raise ConfigError("kv_studio.window_title_pattern must not be empty")


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    apply_enabled: bool = False
    require_checkpoint: bool = True
    forbid_online_operations: bool = True
    batch_size: int = 25

    def validate(self) -> None:
        if not 1 <= self.batch_size <= 500:
            raise ConfigError("safety.batch_size must be between 1 and 500")
        if not self.forbid_online_operations:
            raise ConfigError("online PLC operations cannot be enabled by configuration")


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    llm: LlmConfig = field(default_factory=LlmConfig)
    kv_studio: KvStudioConfig = field(default_factory=KvStudioConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    _secrets: _SecretValues = field(
        default_factory=lambda: _SecretValues(None, None),
        repr=False,
        compare=False,
    )

    def validate(self) -> None:
        self.llm.validate()
        self.kv_studio.validate()
        self.safety.validate()

    @property
    def worker_token(self) -> str | None:
        return self._secrets.worker_token


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a JSON object")
    return value


def _parse_env_value(raw: str, *, line_number: int) -> str:
    value = raw.strip()
    if value[:1] in {"\"", "'"}:
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise ConfigError(f"invalid quoted value in .env at line {line_number}")
        value = value[1:-1]
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise ConfigError(f"control character in .env at line {line_number}")
    return value


def _load_env_file(
    path: Path,
    *,
    required: bool,
    allowed_names: set[str],
) -> dict[str, str]:
    try:
        if path.stat().st_size > _MAX_ENV_FILE_BYTES:
            raise ConfigError(f".env file is larger than {_MAX_ENV_FILE_BYTES} bytes")
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        if required:
            raise ConfigError(f"cannot load environment file {path}: file does not exist") from None
        return {}
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot load environment file {path}: {exc}") from exc

    if any(
        (ord(character) < 32 and character not in {"\r", "\n"})
        or 127 <= ord(character) <= 159
        for character in text
    ):
        raise ConfigError("environment file contains an unsupported control character")

    parsed: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise ConfigError(f"invalid .env assignment at line {line_number}")
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if not _ENV_NAME_RE.fullmatch(name):
            raise ConfigError(f"invalid variable name in .env at line {line_number}")
        if name not in allowed_names:
            raise ConfigError(f"unsupported variable {name} in .env at line {line_number}")
        if name in parsed:
            raise ConfigError(f"duplicate variable {name} in .env at line {line_number}")
        parsed[name] = _parse_env_value(raw_value, line_number=line_number)
    return parsed


def _validate_secret(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise ConfigError(f"{name} must not contain control characters")
    return value


def load_config(
    path: str | Path | None = None,
    env_path: str | Path | None = None,
) -> RuntimeConfig:
    """Load safe JSON settings and local secrets/LLM settings from ``.env``."""

    raw: dict[str, Any] = {}
    config_path: Path | None = None
    if path is not None:
        config_path = Path(path).resolve()
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot load configuration {config_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError("configuration root must be a JSON object")
        raw = loaded

    llm_data = dict(_section(raw, "llm"))
    if "api_key" in llm_data or "_api_key" in llm_data:
        raise ConfigError("llm API keys must be stored in .env, not JSON")
    if (
        "api_key_env" in llm_data
        and llm_data.pop("api_key_env") != DEFAULT_LLM_API_KEY_ENV
    ):
        raise ConfigError(f"llm.api_key_env may only be {DEFAULT_LLM_API_KEY_ENV}")

    resolved_env_path = None
    if env_path is not None:
        resolved_env_path = Path(env_path).resolve()
    elif config_path is not None:
        resolved_env_path = config_path.parent / ".env"
    allowed_names = {
        LLM_ENDPOINT_ENV,
        LLM_MODEL_ENV,
        WORKER_TOKEN_ENV,
        DEFAULT_LLM_API_KEY_ENV,
    }
    file_environment = (
        _load_env_file(
            resolved_env_path,
            required=env_path is not None,
            allowed_names=allowed_names,
        )
        if resolved_env_path is not None
        else {}
    )
    process_environment = {
        name: os.environ[name] for name in allowed_names if name in os.environ
    }
    environment = {**file_environment, **process_environment}

    if LLM_ENDPOINT_ENV in environment:
        llm_data["endpoint"] = environment[LLM_ENDPOINT_ENV]
    if LLM_MODEL_ENV in environment:
        llm_data["model"] = environment[LLM_MODEL_ENV]

    secrets = _SecretValues(
        _validate_secret(environment.get(DEFAULT_LLM_API_KEY_ENV), DEFAULT_LLM_API_KEY_ENV),
        _validate_secret(environment.get(WORKER_TOKEN_ENV), WORKER_TOKEN_ENV),
    )

    try:
        config = RuntimeConfig(
            llm=LlmConfig(**llm_data, _secrets=secrets),
            kv_studio=KvStudioConfig(**_section(raw, "kv_studio")),
            safety=SafetyConfig(**_section(raw, "safety")),
            _secrets=secrets,
        )
    except TypeError as exc:
        raise ConfigError(f"unknown or missing configuration field: {exc}") from exc
    config.validate()
    return config
