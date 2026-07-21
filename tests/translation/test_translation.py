from __future__ import annotations

import unittest

from autocomp.translation.client import (
    TranslationProvider,
    _parse_batch_completion,
    _parse_completion,
    _strip_fences,
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
from autocomp.translation.protection import (
    protect_tokens,
    restore_tokens,
    separate_protected_tokens,
    validate_protected_tokens,
)
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


class ReorderingBatchProvider(FakeProvider):
    def translate_batch(
        self, items: list[ProviderBatchItem], *, glossary: dict[str, str]
    ) -> dict[str, ProviderTranslation]:
        del glossary
        return {
            item.record_id: ProviderTranslation(
                "Start [[PLC_TOKEN_1]] [[PLC_TOKEN_0]]"
                if "PLC_TOKEN_1" in item.text
                else item.text.replace("停止", "Stop")
            )
            for item in items
        }


class AlwaysReorderingMarkerProvider(ReorderingBatchProvider):
    def translate(
        self, text: str, *, context: str, glossary: dict[str, str]
    ) -> ProviderTranslation:
        del context, glossary
        if "PLC_TOKEN_1" in text:
            return ProviderTranslation("Start [[PLC_TOKEN_1]] [[PLC_TOKEN_0]]")
        return ProviderTranslation(text.replace("启动", "Start"))


class TranslationTests(unittest.TestCase):
    def test_detects_cjk_and_flags_string_literal(self) -> None:
        self.assertTrue(contains_cjk("电机启动"))
        self.assertFalse(contains_cjk("Motor start"))
        risk, review, _ = assess_risk(TextKind.STRING_LITERAL, "启动")
        self.assertEqual(risk, RiskLevel.HIGH)
        self.assertTrue(review)

    def test_block_comment_wrapper_is_not_misclassified_as_a_path(self) -> None:
        risk, review, _ = assess_risk(TextKind.COMMENT, "/*报警*/")
        self.assertEqual(risk, RiskLevel.LOW)
        self.assertFalse(review)

    def test_protection_requires_exact_marker_roundtrip(self) -> None:
        protected = protect_tokens("启动 X0 后 MOV DM100 10")
        self.assertEqual(
            restore_tokens(protected.text, protected.tokens), "启动 X0 后 MOV DM100 10"
        )
        with self.assertRaises(ValueError):
            restore_tokens("Start", protected.tokens)

    def test_protection_preserves_mixed_project_identifiers_and_markers(self) -> None:
        source = "/*A_30号指令:@MR22615~MR23115...; DM??; MQTT:4G; 1#→2#*/"

        protected = protect_tokens(source)

        self.assertEqual(restore_tokens(protected.text, protected.tokens), source)
        for token in (
            "/*",
            "A_30",
            "@MR22615~MR23115...",
            "DM??",
            "MQTT:4G",
            "1#",
            "→",
            "2#",
            "*/",
        ):
            self.assertIn(token, protected.tokens)

    def test_protection_rejects_reserved_or_reordered_markers(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved"):
            protect_tokens("启动 [[PLC_TOKEN_0]]")

        protected = protect_tokens("启动 X0 DM100")
        with self.assertRaisesRegex(ValueError, "reordered"):
            restore_tokens("Start [[PLC_TOKEN_1]] [[PLC_TOKEN_0]]", protected.tokens)

    def test_protection_keeps_compound_ascii_atoms_whole(self) -> None:
        cases = {
            "A50Command:（2#、3#）": ("A50Command", "2#", "3#"),
            "报警映射（MR--DM102）": ("MR--DM102",),
            "MQTT:4G通信模块": ("MQTT:4G",),
            "兼容1.0版本的56#指令": ("1.0", "56#"),
            "PLC:DM2000；Z1=8": ("PLC:DM2000", "Z1=8"),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                protected = protect_tokens(source)
                self.assertEqual(protected.tokens, expected)
                self.assertEqual(restore_tokens(protected.text, protected.tokens), source)

    def test_karat_gold_k_is_translatable_domain_text(self) -> None:
        protected = protect_tokens("A_31号指令:去K金位")
        self.assertEqual(protected.tokens, ("A_31",))
        self.assertIn("K金位", protected.text)
        self.assertEqual(
            validate_protected_tokens(
                "A_31号指令:去K金位", "A_31 Command: Go to Karat-Gold Station"
            ),
            ("A_31",),
        )

    def test_exact_translation_must_preserve_tokens_and_inter_token_gaps(self) -> None:
        source = "/*熔炼*/@MR0、@DM0"
        self.assertEqual(
            validate_protected_tokens(source, "/*Induction Melting*/ @MR0, @DM0"),
            ("/*", "*/", "@MR0", "@DM0"),
        )
        with self.assertRaisesRegex(ValueError, "missing or reordered"):
            validate_protected_tokens("MQTT:4G通信", "MQTT: 4G Communication")
        with self.assertRaisesRegex(ValueError, "were joined"):
            validate_protected_tokens(source, "/*Induction Melting*/@MR0@DM0")
        with self.assertRaisesRegex(ValueError, "joined on the right"):
            validate_protected_tokens("/*XYZ轴初始化*/", "/*XYZAxis Initialization*/")
        with self.assertRaisesRegex(ValueError, "joined on the right"):
            validate_protected_tokens("/*11#载盘*/", "/*11#Carrier Tray*/")
        self.assertEqual(validate_protected_tokens("A_粗称去皮", "A_Tare Coarse Scale"), ("A_",))

    def test_provider_token_boundaries_are_repaired_deterministically(self) -> None:
        cases = {
            "/*XYZ轴初始化*/": ("/*XYZAxis Initialization*/", "/*XYZ Axis Initialization*/"),
            "/*11#载盘*/": ("/*11#Carrier Tray*/", "/*11# Carrier Tray*/"),
            "位置R508、R509": (
                "PositionR508R509",
                "Position R508, R509",
            ),
        }
        for source, (target, expected) in cases.items():
            with self.subTest(source=source):
                repaired = separate_protected_tokens(source, target)
                self.assertEqual(repaired, expected)
                validate_protected_tokens(source, repaired)

        omitted_segment = separate_protected_tokens("X0启动DM0", "X0DM0")
        self.assertEqual(omitted_segment, "X0DM0")
        with self.assertRaisesRegex(ValueError, "were joined"):
            validate_protected_tokens("X0启动DM0", omitted_segment)

    def test_glossary_and_memory_hits_are_token_validated(self) -> None:
        record = InventoryRecord("one", "Port0与精称通信", TextKind.COMMENT)
        with self.assertRaisesRegex(ValueError, "missing or reordered"):
            TranslationService(
                FakeProvider(), glossary=Glossary({record.source_text: "Port 0 Fine Scale"})
            ).propose(record)

        memory = TranslationMemory()
        memory.remember(record.source_text, "Port 0 Fine Scale")
        with self.assertRaisesRegex(ValueError, "missing or reordered"):
            TranslationService(FakeProvider(), memory=memory).propose(record)

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

    def test_provider_metadata_is_safely_canonicalized(self) -> None:
        parsed = _parse_completion(
            {
                "choices": [
                    {
                        "message": {
                            "content": {
                                "translation": "Alarm",
                                "notes": None,
                                "confidence": "0.95",
                            }
                        }
                    }
                ]
            }
        )
        self.assertEqual(parsed.notes, "")
        self.assertEqual(parsed.confidence, 0.95)

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

    def test_invalid_batch_placeholder_order_retries_one_record(self) -> None:
        provider = ReorderingBatchProvider()
        service = TranslationService(provider, batch_size=2)

        decisions = service.propose_batch(
            [
                InventoryRecord("one", "启动 X0 DM100", TextKind.COMMENT),
                InventoryRecord("two", "停止", TextKind.COMMENT),
            ]
        )

        self.assertEqual(decisions[0].target_text, "Start X0 DM100")
        self.assertEqual(decisions[0].provider, "individual_provider_retry")
        self.assertEqual(decisions[1].target_text, "Stop")

    def test_repeated_marker_failure_translates_only_intervening_segments(self) -> None:
        provider = AlwaysReorderingMarkerProvider()
        service = TranslationService(provider, batch_size=2)

        decisions = service.propose_batch(
            [
                InventoryRecord("one", "/*启动*/ X0", TextKind.COMMENT),
                InventoryRecord("two", "停止", TextKind.COMMENT),
            ]
        )

        self.assertEqual(decisions[0].target_text, "/*Start*/ X0")
        self.assertEqual(decisions[0].provider, "segmented_provider_retry")

    def test_batch_parser_rejects_unknown_or_missing_ids(self) -> None:
        payload = {
            "choices": [
                {"message": {"content": '{"items":[{"record_id":"wrong","translation":"Start"}]}'}}
            ]
        }
        with self.assertRaises(ValueError):
            _parse_batch_completion(payload, {"expected"})

    def test_qwen_thinking_wrapper_is_removed_before_json_parsing(self) -> None:
        wrapped = '<think>internal</think>\n```json\n{"items": []}\n```'
        self.assertEqual(_strip_fences(wrapped), '{"items": []}')

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
