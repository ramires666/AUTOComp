from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from autocomp.translation.models import ProviderTranslation


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts/translate-english-spans-resumable.py"
    spec = importlib.util.spec_from_file_location("translate_english_spans_resumable", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


class _FakeProvider:
    calls = 0

    def translate_batch(self, items, *, glossary):  # type: ignore[no-untyped-def]
        self.calls += 1
        return {
            item.record_id: ProviderTranslation("New Label")
            for item in items
        }


def test_seeds_skips_batches_and_resumes_without_network(tmp_path: Path) -> None:
    inventory = {
        "items": [
            {"id": "reuse", "source": "报警", "kind": "comment_or_label", "reuse_target": "Alarm"},
            {
                "id": "glossary",
                "source": "冷水机",
                "kind": "comment_or_label",
                "reuse_target": None,
            },
            {"id": "llm", "source": "新标签", "kind": "comment_or_label", "reuse_target": None},
            {"id": "voice", "source": "请稍候", "kind": "operator_voice_string"},
            {"id": "module", "source": "通信程序", "kind": "program_module_name"},
        ]
    }
    inventory_path = tmp_path / "inventory.json"
    glossary_path = tmp_path / "glossary.json"
    memory_path = tmp_path / "memory.json"
    output_path = tmp_path / "translations.json"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
    glossary_path.write_text(
        json.dumps({"冷水机": "Chiller"}, ensure_ascii=False), encoding="utf-8"
    )
    memory_path.write_text("{}", encoding="utf-8")

    provider = _FakeProvider()
    first = SCRIPT.run(
        inventory_path=inventory_path,
        output_path=output_path,
        glossary_path=glossary_path,
        memory_path=memory_path,
        max_batches=1,
        provider=provider,
        project_context="Precious-metal kiosk PLC.",
    )
    second = SCRIPT.run(
        inventory_path=inventory_path,
        output_path=output_path,
        glossary_path=glossary_path,
        memory_path=memory_path,
        max_batches=1,
        provider=provider,
        project_context="Precious-metal kiosk PLC.",
    )

    assert provider.calls == 1
    assert first["translations"] == {
        "报警": "Alarm",
        "冷水机": "Chiller",
        "新标签": "New Label",
    }
    assert second["translations"] == first["translations"]
    assert second["progress"]["complete"] is True
    assert second["progress"]["batches_completed_total"] == 1
    assert second["progress"]["skipped"] == {
        "program_module_name": 1,
        "operator_voice_string": 1,
    }
