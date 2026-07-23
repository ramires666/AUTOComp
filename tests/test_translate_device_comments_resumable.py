from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from autocomp.translation.models import ProviderTranslation


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts/translate-device-comments-resumable.py"
    spec = importlib.util.spec_from_file_location("translate_device_comments_resumable", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


class _FakeProvider:
    calls = 0

    def translate_batch(self, items, *, glossary):  # type: ignore[no-untyped-def]
        self.calls += 1
        assert glossary == {}
        assert "Mnemonic and neighboring-comment context" in items[0].context
        assert "LD R100" in items[0].context
        if self.calls == 1:
            return {
                item.record_id: ProviderTranslation("ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJK")
                for item in items
            }
        assert "REPAIR REQUIRED" in items[0].context
        return {item.record_id: ProviderTranslation("Robot Arm Ready") for item in items}


def test_translates_with_constraints_and_resumes_without_network(tmp_path: Path) -> None:
    inventory = {
        "items": [
            {
                "id": "seed",
                "source": "报警",
                "addresses": ["MR100"],
                "context_samples": [{"instruction": "OUT MR100"}],
                "reuse_target": "Alarm",
            },
            {
                "id": "llm",
                "source": "机械手准备完成",
                "rows": [12],
                "context": {
                    "mnemonic": "LD R100",
                    "neighbor_comments": ["机械手启动"],
                },
            },
        ]
    }
    inventory_path = tmp_path / "inventory.json"
    glossary_path = tmp_path / "glossary.json"
    output_path = tmp_path / "translations.json"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
    glossary_path.write_text("{}", encoding="utf-8")

    provider = _FakeProvider()
    first = SCRIPT.run(
        inventory_path=inventory_path,
        output_path=output_path,
        glossary_path=glossary_path,
        batch_size=50,
        max_batches=1,
        provider=provider,
        project_context="Precious-metal kiosk PLC with robot arm and XRF.",
    )
    second = SCRIPT.run(
        inventory_path=inventory_path,
        output_path=output_path,
        glossary_path=glossary_path,
        batch_size=50,
        max_batches=1,
        provider=provider,
        project_context="Precious-metal kiosk PLC with robot arm and XRF.",
    )

    assert provider.calls == 2
    assert first["translations"] == {
        "报警": "Alarm",
        "机械手准备完成": "Robot Arm Ready",
    }
    assert second["translations"] == first["translations"]
    assert second["progress"]["complete"] is True
    assert second["progress"]["batches_completed_total"] == 1
    with pytest.raises(ValueError, match="at most 32"):
        SCRIPT._validate_target("源", "bad,comment", "test")
