from __future__ import annotations

import unittest

from autocomp.translation.client import (
    TranslationProvider,
    _parse_batch_completion,
    _parse_completion,
)
from autocomp.translation.inventory import assess_risk, contains_cjk
from autocomp.translation.memory import Glossary, TranslationMemory
from autocomp.translation.models import (
    InventoryRecord,
    ProviderBatchItem,
    ProviderTranslation,
    RiskLevel,
    TextKind,
)
from autocomp.translation.protection import protect_tokens, restore_tokens
from autocomp.translation.service import TranslationService


class FakeProvider(TranslationProvider):
    def __init__(self) -> None:
        self.calls = 0

    def translate(
        self, text: str, *, context: str, glossary: dict[str, str]
    ) -> ProviderTranslation:
        self.calls += 1
        return ProviderTranslation(text.replace("启动", "Start"))


class BatchProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.batch_calls = 0

    def translate_batch(
        self, items: list[ProviderBatchItem], *, glossary: dict[str, str]
    ) -> dict[str, ProviderTranslation]:
        self.batch_calls += 1
        return {
            item.record_id: ProviderTranslation(
                item.text.replace("启动", "Start").replace("停止", "Stop")
            )
            for item in items
        }


class UntranslatedProvider(FakeProvider):
    def translate(
        self, text: str, *, context: str, glossary: dict[str, str]
    ) -> ProviderTranslation:
        return ProviderTranslation(text)


class TranslationTests(unittest.TestCase):
    def test_detects_cjk_and_flags_string_literal(self) -> None:
        self.assertTrue(contains_cjk("电机启动"))
        self.assertFalse(contains_cjk("Motor start"))
        risk, review, _ = assess_risk(TextKind.STRING_LITERAL, "启动")
        self.assertEqual(risk, RiskLevel.HIGH)
        self.assertTrue(review)

    def test_protection_requires_exact_marker_roundtrip(self) -> None:
        protected = protect_tokens("启动 X0 后 MOV DM100 10")
        self.assertEqual(
            restore_tokens(protected.text, protected.tokens), "启动 X0 后 MOV DM100 10"
        )
        with self.assertRaises(ValueError):
            restore_tokens("Start", protected.tokens)

    def test_memory_deduplicates_provider_requests(self) -> None:
        provider = FakeProvider()
        service = TranslationService(provider, memory=TranslationMemory())
        record = InventoryRecord("a", "启动", TextKind.COMMENT)
        self.assertEqual(service.propose(record).target_text, "Start")
        self.assertEqual(
            service.propose(InventoryRecord("b", "启动", TextKind.COMMENT)).provider, "memory"
        )
        self.assertEqual(provider.calls, 1)

    def test_glossary_wins(self) -> None:
        provider = FakeProvider()
        service = TranslationService(provider, glossary=Glossary({"启动": "Run"}))
        decision = service.propose(InventoryRecord("a", "启动", TextKind.COMMENT))
        self.assertEqual(decision.target_text, "Run")
        self.assertEqual(decision.provider, "glossary")
        self.assertEqual(provider.calls, 0)

    def test_rejects_invalid_provider_shape(self) -> None:
        with self.assertRaises(ValueError):
            _parse_completion({"choices": [{"message": {"content": '{"translation": 3}'}}]})

    def test_batch_deduplicates_and_preserves_protected_tokens(self) -> None:
        provider = BatchProvider()
        service = TranslationService(provider, batch_size=2)
        records = [
            InventoryRecord("one", "启动 X0", TextKind.COMMENT),
            InventoryRecord("two", "启动 X0", TextKind.COMMENT),
            InventoryRecord(
                "three",
                "停止 DM100",
                TextKind.STRING_LITERAL,
                risk=RiskLevel.HIGH,
                requires_review=True,
            ),
        ]
        decisions = service.propose_batch(records)
        self.assertEqual(provider.batch_calls, 1)
        self.assertEqual(
            [item.target_text for item in decisions], ["Start X0", "Start X0", "Stop DM100"]
        )
        self.assertTrue(decisions[2].requires_review)
        self.assertEqual(decisions[0].protected_tokens, ("X0",))

    def test_batch_parser_rejects_unknown_or_missing_ids(self) -> None:
        payload = {
            "choices": [
                {"message": {"content": '{"items":[{"record_id":"wrong","translation":"Start"}]}'}}
            ]
        }
        with self.assertRaises(ValueError):
            _parse_batch_completion(payload, {"expected"})

    def test_batch_rejects_duplicate_record_ids_even_when_sources_differ(self) -> None:
        service = TranslationService(BatchProvider())
        with self.assertRaisesRegex(ValueError, "duplicate record_id"):
            service.propose_batch(
                [
                    InventoryRecord("same", "启动", TextKind.COMMENT),
                    InventoryRecord("same", "停止", TextKind.COMMENT),
                ]
            )

    def test_provider_must_remove_all_cjk_from_target(self) -> None:
        service = TranslationService(UntranslatedProvider())
        with self.assertRaisesRegex(ValueError, "still contains CJK"):
            service.propose(InventoryRecord("one", "启动", TextKind.COMMENT))


if __name__ == "__main__":
    unittest.main()
