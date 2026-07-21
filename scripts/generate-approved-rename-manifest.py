"""Generate an approved UI rename manifest for bookmark headings from reviewed proposals.

This is a one-off script for the current AUTOComp checkpoint 03. It intentionally
excludes program names because they remain blocked by the length/charset pilot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

project = Path(__file__).resolve().parent.parent
inventory_path = project / "reports" / "02-tree-translation-inventory.json"
manifest_path = project / "reports" / "02-tree-translation-manifest.json"
output_path = project / "reports" / "03-approved-ui-rename-manifest.json"

inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

decisions = {decision["record_id"]: decision for decision in manifest["decisions"]}

bookmark_items = []
skipped_program_names = []
for record in inventory:
    if record["kind"] == "program_name":
        skipped_program_names.append(record["source_text"])
        continue
    decision = decisions.get(record["record_id"])
    if decision is None:
        continue
    locator_match = re.search(r"locator=(\d+(?:\.\d+)*)", record["context"])
    if not locator_match:
        continue
    locator = [int(part) for part in locator_match.group(1).split(".")]
    expected_path = [part for part in record["hierarchy"] if not part.startswith("locator:")]
    bookmark_items.append(
        {
            "record_id": record["record_id"],
            "locator": locator,
            "expected_path": expected_path,
            "expected_source": record["source_text"],
            "target": decision["target_text"],
            "risk": record["risk"],
            "requires_review": record["requires_review"],
        }
    )

payload = {
    "schema_version": 1,
    "artifact_type": "approved_ui_rename_manifest",
    "checkpoint": "03-bookmarks-approved",
    "apply_gate": {
        "apply_enabled": True,
        "requires_explicit_apply_flag": True,
        "requires_named_checkpoint": True,
        "program_names_excluded": True,
        "reason": (
            "Program names remain blocked pending disposable-project "
            "length and charset pilot."
        ),
    },
    "source_artifacts": {
        "translation_inventory": inventory_path.name,
        "translation_manifest": manifest_path.name,
    },
    "summary": {
        "total_inventory_records": len(inventory),
        "bookmark_rename_items": len(bookmark_items),
        "excluded_program_names": len(skipped_program_names),
        "high_risk_items": sum(1 for item in bookmark_items if item["risk"] == "high"),
        "review_required_items": sum(
            1 for item in bookmark_items if item["requires_review"]
        ),
    },
    "items": bookmark_items,
}

output_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"Wrote {output_path.name}: {len(bookmark_items)} bookmark rename items")
