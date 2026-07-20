"""Minimal OpenAI-compatible HTTP client with strict response validation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import ProviderBatchItem, ProviderTranslation

_TRANSLATION_PROPERTIES: dict[str, Any] = {
    "translation": {"type": "string", "minLength": 1},
    "notes": {"type": "string"},
    "confidence": {"type": ["number", "null"]},
}
_SINGLE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": _TRANSLATION_PROPERTIES,
        "required": ["translation", "notes", "confidence"],
        "additionalProperties": False,
    },
}
_BATCH_RESPONSE_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "record_id": {"type": "string", "minLength": 1},
                        **_TRANSLATION_PROPERTIES,
                    },
                    "required": ["record_id", "translation", "notes", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    },
}


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
        self._effective_model: str | None = None
        self._candidate_models: list[str] = []

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
            "response_format": _SINGLE_RESPONSE_FORMAT,
        }
        return _parse_completion(self._post(payload))

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
            "response_format": _BATCH_RESPONSE_FORMAT,
        }
        body = self._post(payload)
        return _parse_batch_completion(body, {item.record_id for item in items})

    def _post(self, payload: dict[str, Any]) -> Any:
        if not self._auto_model:
            return self._post_with_model(payload, self._config.model)

        tried: set[str] = set()
        last_error: Exception | None = None
        for refresh in (False, True):
            candidates = self._models(force_refresh=refresh)
            for model in candidates:
                if model in tried:
                    continue
                tried.add(model)
                try:
                    result = self._post_with_model(payload, model)
                except _HttpRequestError as exc:
                    last_error = exc
                    if exc.status not in {400, 404, 422}:
                        raise RuntimeError("translation provider request failed") from exc
                    continue
                self._effective_model = model
                self._candidate_models = [model, *[item for item in candidates if item != model]]
                return result
        raise RuntimeError("no responding chat model was found at the LLM endpoint") from last_error

    @property
    def _auto_model(self) -> bool:
        return self._config.model.strip().casefold() == "auto"

    def _models(self, *, force_refresh: bool) -> list[str]:
        if self._candidate_models and not force_refresh:
            return list(self._candidate_models)
        url = self._config.base_url.rstrip("/") + "/models"
        try:
            payload = self._request_json(Request(url, headers=self._headers(), method="GET"))
        except (_HttpRequestError, URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError(
                "automatic model discovery failed; set AUTOCOMP_LLM_MODEL explicitly"
            ) from exc
        candidates = _parse_model_ids(payload)
        if not candidates:
            raise RuntimeError(
                "the LLM endpoint advertises no usable models; set AUTOCOMP_LLM_MODEL explicitly"
            )
        self._candidate_models = candidates
        return list(candidates)

    def _post_with_model(self, payload: dict[str, Any], model: str) -> Any:
        body = {**payload, "model": model}
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        variants = [body]
        if "response_format" in body:
            variants.append({key: value for key, value in body.items() if key != "response_format"})
        for index, variant in enumerate(variants):
            request = Request(
                url,
                data=json.dumps(variant).encode("utf-8"),
                headers=self._headers(),
                method="POST",
            )
            try:
                return self._request_json(request)
            except _HttpRequestError as exc:
                if index == 0 and len(variants) > 1 and exc.status in {400, 422}:
                    continue
                raise
            except (URLError, TimeoutError, ValueError) as exc:
                raise RuntimeError("translation provider request failed") from exc
        raise RuntimeError("translation provider request failed")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _request_json(self, request: Request) -> Any:
        try:
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except HTTPError as exc:
            raise _HttpRequestError(exc.code) from exc
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("LLM response is too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("LLM response is not valid UTF-8 JSON") from exc


class _HttpRequestError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"LLM endpoint returned HTTP {status}")
        self.status = status


def _parse_model_ids(payload: Any) -> list[str]:
    entries: Any
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        entries = payload["data"]
    elif isinstance(payload, dict) and isinstance(payload.get("models"), list):
        entries = payload["models"]
    elif isinstance(payload, list):
        entries = payload
    else:
        return []

    discovered: list[str] = []
    for entry in entries[:32]:
        if isinstance(entry, str):
            model_id = entry
        elif isinstance(entry, dict):
            model_id = next(
                (
                    value
                    for key in ("id", "model", "name")
                    if isinstance((value := entry.get(key)), str)
                ),
                "",
            )
        else:
            continue
        model_id = model_id.strip()
        if (
            not model_id
            or len(model_id) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in model_id)
            or model_id in discovered
        ):
            continue
        discovered.append(model_id)

    non_chat_markers = ("embed", "rerank", "whisper", "tts", "moderation", "image")
    return sorted(
        discovered,
        key=lambda model_id: (
            any(marker in model_id.casefold() for marker in non_chat_markers),
            discovered.index(model_id),
        ),
    )


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
