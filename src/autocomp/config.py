"""Runtime configuration with secrets supplied through environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Raised when a configuration file is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class LlmConfig:
    endpoint: str = "http://127.0.0.1:8000/v1"
    model: str = "local-vision-model"
    timeout_seconds: float = 120.0
    api_key_env: str = "AUTOCOMP_LLM_API_KEY"

    def validate(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("llm.endpoint must be an absolute HTTP(S) URL")
        if not self.model.strip():
            raise ConfigError("llm.model must not be empty")
        if not 1 <= self.timeout_seconds <= 600:
            raise ConfigError("llm.timeout_seconds must be between 1 and 600")

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


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

    def validate(self) -> None:
        self.llm.validate()
        self.kv_studio.validate()
        self.safety.validate()


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a JSON object")
    return value


def load_config(path: str | Path | None = None) -> RuntimeConfig:
    """Load configuration from JSON, or return validated safe defaults."""

    raw: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot load configuration {config_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError("configuration root must be a JSON object")
        raw = loaded

    try:
        config = RuntimeConfig(
            llm=LlmConfig(**_section(raw, "llm")),
            kv_studio=KvStudioConfig(**_section(raw, "kv_studio")),
            safety=SafetyConfig(**_section(raw, "safety")),
        )
    except TypeError as exc:
        raise ConfigError(f"unknown or missing configuration field: {exc}") from exc
    config.validate()
    return config
