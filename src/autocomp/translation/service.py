"""Safe orchestration for inventory records; this module performs no writes."""

from __future__ import annotations

import re

from .client import TranslationProvider
from .inventory import contains_cjk
from .memory import Glossary, TranslationMemory
from .models import InventoryRecord, ProviderBatchItem, TranslationDecision
from .protection import (
    ProtectedText,
    protect_tokens,
    restore_tokens,
    separate_protected_tokens,
    validate_protected_tokens,
)

_PROTECTED_MARKER_SPLIT_RE = re.compile(r"(\[\[PLC_TOKEN_\d+\]\])")


class TranslationService:
    def __init__(
        self,
        provider: TranslationProvider,
        *,
        glossary: Glossary | None = None,
        memory: TranslationMemory | None = None,
        batch_size: int = 20,
    ) -> None:
        self._provider = provider
        self._glossary = glossary or Glossary()
        self._memory = memory or TranslationMemory()
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._batch_size = batch_size

    def propose(self, record: InventoryRecord) -> TranslationDecision:
        if not contains_cjk(record.source_text):
            return TranslationDecision(
                record.record_id,
                record.source_text,
                record.source_text,
                "skipped",
                record.risk,
                record.requires_review,
                "no CJK text",
            )
        protected = protect_tokens(record.source_text)
        target = self._glossary.lookup(record.source_text)
        origin = "glossary"
        if target is None:
            target = self._memory.lookup(record.source_text)
            origin = "memory"
        if target is None:
            response = self._provider.translate(
                protected.text, context=record.context, glossary=self._glossary.terms
            )
            target = restore_tokens(response.translation, protected.tokens)
            target = separate_protected_tokens(record.source_text, target)
            self._validate_target(target)
            self._memory.remember(record.source_text, target)
            origin = "provider"
        self._validate_target(target)
        validate_protected_tokens(record.source_text, target)
        return TranslationDecision(
            record.record_id,
            record.source_text,
            target,
            "proposed",
            record.risk,
            record.requires_review,
            provider=origin,
            protected_tokens=protected.tokens,
        )

    def propose_batch(self, records: list[InventoryRecord]) -> list[TranslationDecision]:
        """Generate dry-run proposals while deduplicating provider work by source text."""
        results: dict[str, TranslationDecision] = {}
        pending: dict[str, tuple[InventoryRecord, ProtectedText]] = {}
        provider_origins: dict[str, str] = {}
        seen_ids: set[str] = set()
        for record in records:
            if record.record_id in seen_ids:
                raise ValueError(f"duplicate record_id: {record.record_id}")
            seen_ids.add(record.record_id)
            if not contains_cjk(record.source_text):
                results[record.record_id] = TranslationDecision(
                    record.record_id,
                    record.source_text,
                    record.source_text,
                    "skipped",
                    record.risk,
                    record.requires_review,
                    "no CJK text",
                )
                continue
            target = self._glossary.lookup(record.source_text)
            origin = "glossary"
            if target is None:
                target = self._memory.lookup(record.source_text)
                origin = "memory"
            if target is not None:
                self._validate_target(target)
                protected_tokens = validate_protected_tokens(record.source_text, target)
                results[record.record_id] = TranslationDecision(
                    record.record_id,
                    record.source_text,
                    target,
                    "proposed",
                    record.risk,
                    record.requires_review,
                    provider=origin,
                    protected_tokens=protected_tokens,
                )
                continue
            # Request each source phrase once. Duplicate records reuse translation memory.
            if record.source_text not in pending:
                pending[record.source_text] = (record, protect_tokens(record.source_text))

        unresolved = list(pending.values())
        for start in range(0, len(unresolved), self._batch_size):
            group = unresolved[start : start + self._batch_size]
            batch_items = [
                ProviderBatchItem(record.record_id, protected.text, record.context)
                for record, protected in group
            ]
            response = self._provider.translate_batch(batch_items, glossary=self._glossary.terms)
            for record, protected in group:
                try:
                    target = restore_tokens(
                        response[record.record_id].translation,
                        protected.tokens,
                    )
                    target = separate_protected_tokens(record.source_text, target)
                except ValueError:
                    retry = self._provider.translate(
                        protected.text,
                        context=(
                            record.context + "; retry after batch placeholder validation failure; "
                            "preserve placeholders in exact ascending order"
                        ),
                        glossary=self._glossary.terms,
                    )
                    try:
                        target = restore_tokens(retry.translation, protected.tokens)
                        target = separate_protected_tokens(record.source_text, target)
                    except ValueError:
                        target = self._translate_protected_segments(record, protected)
                        provider_origins[record.record_id] = "segmented_provider_retry"
                    else:
                        provider_origins[record.record_id] = "individual_provider_retry"
                else:
                    provider_origins[record.record_id] = "batch_provider"
                self._validate_target(target)
                validate_protected_tokens(record.source_text, target)
                self._memory.remember(record.source_text, target)

        for record in records:
            if record.record_id in results:
                continue
            target = self._memory.lookup(record.source_text)
            if (
                target is None
            ):  # Defensive: provider response validation should make this impossible.
                raise RuntimeError("translation memory missing completed batch item")
            self._validate_target(target)
            protected = protect_tokens(record.source_text)
            validate_protected_tokens(record.source_text, target)
            results[record.record_id] = TranslationDecision(
                record.record_id,
                record.source_text,
                target,
                "proposed",
                record.risk,
                record.requires_review,
                provider=provider_origins.get(record.record_id, "translation_memory"),
                protected_tokens=protected.tokens,
            )
        return [results[record.record_id] for record in records]

    def _translate_protected_segments(
        self,
        record: InventoryRecord,
        protected: ProtectedText,
    ) -> str:
        parts = _PROTECTED_MARKER_SPLIT_RE.split(protected.text)
        translated_parts: list[str] = []
        segment_number = 0
        for part in parts:
            if not part or _PROTECTED_MARKER_SPLIT_RE.fullmatch(part):
                translated_parts.append(part)
                continue
            if not contains_cjk(part):
                translated_parts.append(part)
                continue
            segment_number += 1
            response = self._provider.translate(
                part,
                context=(
                    record.context
                    + f"; protected-text segment {segment_number}; translate only this segment"
                ),
                glossary=self._glossary.terms,
            )
            if "[[PLC_TOKEN_" in response.translation:
                raise ValueError(
                    "provider inserted a protected marker while translating a plain segment"
                )
            self._validate_target(response.translation)
            translated_parts.append(response.translation)
        target = restore_tokens("".join(translated_parts), protected.tokens)
        return separate_protected_tokens(record.source_text, target)

    @staticmethod
    def _validate_target(target: str) -> None:
        if contains_cjk(target):
            raise ValueError("translation target still contains CJK text")
