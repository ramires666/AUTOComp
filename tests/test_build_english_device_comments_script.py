from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "build-english-device-comments.py"
    spec = importlib.util.spec_from_file_location("build_english_device_comments", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def test_builds_strict_4904_row_ascii_csv_and_reuses_duplicate_translation(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "original-comments.csv"
    translations_path = tmp_path / "translations.json"
    output_path = tmp_path / "english-comments.csv"
    audit_path = tmp_path / "audit.json"
    source_rows = [
        [f"DM{index}", "", "粗称" if index % 2 == 0 else "精称", "", ""]
        for index in range(4904)
    ]
    source_raw = "".join(",".join(row) + "\r\n" for row in source_rows).encode("cp936")
    original_path.write_bytes(source_raw)
    translations_path.write_text(
        json.dumps({"translations": {"粗称": "Coarse Weight", "精称": "Fine Weight"}}),
        encoding="utf-8",
    )

    assert SCRIPT.main(
        [
            str(translations_path),
            "--input",
            str(original_path),
            "--output",
            str(output_path),
            "--audit",
            str(audit_path),
        ]
    ) == 0

    output_raw = output_path.read_bytes()
    output_rows = list(csv.reader(io.StringIO(output_raw.decode("ascii"), newline="")))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert len(output_rows) == 4904
    assert all(len(row) == 5 and row[1] == row[3] == row[4] == "" for row in output_rows)
    assert [row[0] for row in output_rows] == [row[0] for row in source_rows]
    assert output_rows[0][2] == output_rows[2][2] == "Coarse Weight"
    assert output_rows[1][2] == output_rows[3][2] == "Fine Weight"
    assert output_raw.endswith(b"\r\n") and b"\n" not in output_raw.replace(b"\r\n", b"")
    assert not output_raw.startswith(b"\xef\xbb\xbf")
    assert original_path.read_bytes() == source_raw
    assert audit["source"]["immutable"] is True
    assert audit["source"]["sha256"] == hashlib.sha256(source_raw).hexdigest()
    assert audit["invariants"]["duplicate_rows_after_first"] == 4902
    assert len(audit["rows"]) == 4904
