"""Minimal OpenAI-compatible HTTP client with strict response validation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import ProviderBatchItem, ProviderTranslation


class TranslationProvider(ABC):
    @abstractmethod
    def translate(
        self, text: str, *, context: str, glossary: dict[str, str]
    ) -> ProviderTranslation:
        """Return a single validated technical-English translation."""

    def translate_batch(
        self, items: list[ProviderBatchItem], *, glossary: dict[str, str]
    ) -> dict[str, ProviderTranslation]:
        """Translate a batch. Providers may override this to use one request."""
        return {
            item.record_id: self.translate(item.text, context=item.context, glossary=glossary)
            for item in items
        }


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 30.0
    temperature: float = 0.0


class OpenAICompatibleProvider(TranslationProvider):
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self._config = config

    def translate(
        self, text: str, *, context: str, glossary: dict[str, str]
    ) -> ProviderTranslation:
        instruction = (
            "Translate only user-authored Chinese text into concise technical English. "
            "When a glossary source term occurs anywhere in the input, use its target term "
            "verbatim in the translation. Do not leave any CJK characters in the result. "
            "Keep placeholders such as [[PLC_TOKEN_0]] exactly unchanged. "
            "Return a JSON object with translation, notes, and numeric or null confidence fields."
        )
        payload = {
            "model": self._config.model,
            "temperature": self._config.temperature,
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"text": text, "context": context, "glossary": glossary}, ensure_ascii=False
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        request = Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("translation provider request failed") from exc
        return _parse_completion(body)

    def translate_batch(
        self, items: list[ProviderBatchItem], *, glossary: dict[str, str]
    ) -> dict[str, ProviderTranslation]:
        if not items:
            return {}
        instruction = (
            "Translate only user-authored Chinese text into concise technical English. "
            "When a glossary source term occurs anywhere in an input, use its target term "
            "verbatim in that translation. Do not leave any CJK characters in results. "
            "Keep every [[PLC_TOKEN_N]] placeholder exactly unchanged. Return JSON only with "
            "an 'items' array. Each item must have exactly one input record_id plus non-empty "
            "translation, optional string notes, and numeric or null confidence."
        )
        payload = {
            "model": self._config.model,
            "temperature": self._config.temperature,
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "items": [
                                {
                                    "record_id": item.record_id,
                                    "text": item.text,
                                    "context": item.context,
                                }
                                for item in items
                            ],
                            "glossary": glossary,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        body = self._post(payload)
        return _parse_batch_completion(body, {item.record_id for item in items})

    def _post(self, payload: dict[str, Any]) -> Any:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        request = Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("translation provider request failed") from exc


def _parse_completion(payload: Any) -> ProviderTranslation:
    try:
        content = payload["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_fences(content)) if isinstance(content, str) else content
        translation = parsed["translation"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("provider response does not contain a valid JSON translation") from exc
    if not isinstance(translation, str) or not translation.strip():
        raise ValueError("provider translation must be a non-empty string")
    notes = parsed.get("notes", "")
    confidence = parsed.get("confidence")
    if not isinstance(notes, str) or (
        confidence is not None and not isinstance(confidence, (int, float))
    ):
        raise ValueError("provider response fields have invalid types")
    return ProviderTranslation(
        translation=translation,
        notes=notes,
        confidence=float(confidence) if confidence is not None else None,
    )


def _parse_batch_completion(payload: Any, expected_ids: set[str]) -> dict[str, ProviderTranslation]:
    try:
        content = payload["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_fences(content)) if isinstance(content, str) else content
        items = parsed["items"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "provider response does not contain a valid JSON translation batch"
        ) from exc
    if not isinstance(items, list):
        raise ValueError("provider batch items must be a list")
    results: dict[str, ProviderTranslation] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("record_id"), str):
            raise ValueError("provider batch item has no valid record_id")
        record_id = item["record_id"]
        if record_id in results:
            raise ValueError("provider batch contains duplicate record_id")
        # Reuse the single-item schema validator rather than accepting a weaker batch schema.
        results[record_id] = _parse_completion({"choices": [{"message": {"content": item}}]})
    if set(results) != expected_ids:
        raise ValueError("provider batch record IDs do not exactly match the request")
    return results


def _strip_fences(value: str) -> str:
    value = value.strip()
    if value.startswith("```") and value.endswith("```"):
        return value.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return value
