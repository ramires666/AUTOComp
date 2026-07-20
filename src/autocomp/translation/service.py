"""Safe orchestration for inventory records; this module performs no writes."""

from __future__ import annotations

from .client import TranslationProvider
from .inventory import contains_cjk
from .memory import Glossary, TranslationMemory
from .models import InventoryRecord, ProviderBatchItem, TranslationDecision
from .protection import ProtectedText, protect_tokens, restore_tokens


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
            self._validate_target(target)
            self._memory.remember(record.source_text, target)
            origin = "provider"
        self._validate_target(target)
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
                results[record.record_id] = TranslationDecision(
                    record.record_id,
                    record.source_text,
                    target,
                    "proposed",
                    record.risk,
                    record.requires_review,
                    provider=origin,
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
                target = restore_tokens(response[record.record_id].translation, protected.tokens)
                self._validate_target(target)
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
            results[record.record_id] = TranslationDecision(
                record.record_id,
                record.source_text,
                target,
                "proposed",
                record.risk,
                record.requires_review,
                provider="batch_provider",
                protected_tokens=protected.tokens,
            )
        return [results[record.record_id] for record in records]

    @staticmethod
    def _validate_target(target: str) -> None:
        if contains_cjk(target):
            raise ValueError("translation target still contains CJK text")
