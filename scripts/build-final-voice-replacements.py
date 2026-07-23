"""Build exact Chinese-fragment replacements for the remaining voice messages."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports" / "14-node-context-bundles.json"
OUTPUT = ROOT / ".autocomp" / "final-voice-replacements.json"

ENGLISH_MESSAGES = [
    "Welcome to the smart gold kiosk. I am Xiaoya. Let me guide you.",
    "Preparing. Wait.",
    "Gate opening. Place gold in tray center. Do not stack above tray.",
    "Gold now set. Hands away. Gate closing.",
    "Weighing gold. Weight shown on screen.",
    "XRF checking gold purity. Takes 1 min. Results on screen.",
    "Initial weight and purity will appear. To sell tap Next. To cancel tap Return and remove all gold.",
    "Enter ID and bank account, then tap Next.",
    "Melting gold. Takes 4 minutes. Please wait.",
    "Melting done. Cooling inside 2 min. Please wait.",
    "Outside cooling: 2 min. Please wait.",
    "XRF purity recheck: 2 min. Please wait.",
    "Reweighing gold. Please wait.",
    "Final weight and purity will appear. To sell tap Confirm. To cancel tap Return and remove all gold.",
    "Thank you. Payment will be sent to your account. Please use us again.",
    "Cancelled. Gold returning to gate. Remove all gold promptly. Thank you.",
    "Item not accepted. It will return to the gate. Remove it promptly. Thank you.",
    "Gold returning to gate. Remove all promptly. Thank you.",
    "Gold removed. Hands away. Gate closing.",
    "Hands away. Gate closing.",
    "Place gold in weight range.",
    "Place gold in weight range.",
]


def split_text(text: str, capacities: list[int]) -> list[str]:
    if len(text.encode("ascii")) > sum(capacities) or len(text) < len(capacities):
        raise ValueError(f"message does not fit capacities {capacities}: {text}")
    result: list[str] = []
    offset = 0
    for index, capacity in enumerate(capacities):
        remaining_parts = len(capacities) - index - 1
        take = min(capacity, len(text) - offset - remaining_parts)
        result.append(text[offset : offset + take])
        offset += take
    if offset != len(text):
        raise ValueError(
            f"message did not split completely: offset={offset} len={len(text)} "
            f"capacities={capacities} parts={result}: {text}"
        )
    return result


def main() -> None:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    bundle = data["bundles"][33]
    lines = bundle["mnemonic"]["original_lines"]
    all_groups: list[dict[str, object]] = []
    active: dict[str, object] | None = None
    comment_pattern = re.compile(r'DM1300\.T="(\[v10\]\[s[45]\].*?)"')
    instruction_pattern = re.compile(r'^(?:SMOV|SADD)\s+(?:DM\d+\s+)?"(.*?)"\s+DM\d+$')

    for row_number, item in enumerate(lines, 1):
        text = str(item.get("text", ""))
        comment_match = comment_pattern.search(text)
        if row_number >= 1639 and comment_match:
            active = {
                "row": row_number,
                "source_message": comment_match.group(1),
                "source_fragments": [],
            }
            all_groups.append(active)
            continue
        if active is not None:
            instruction_match = instruction_pattern.match(text)
            if instruction_match:
                active["source_fragments"].append(instruction_match.group(1))

    source_groups = [
        group
        for group in all_groups
        if group["source_fragments"]
        and re.search(r"[\u3400-\u9fff]", str(group["source_message"]))
    ]
    if len(source_groups) != len(ENGLISH_MESSAGES):
        raise ValueError(f"expected 22 source groups, found {len(source_groups)}")

    replacements: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []
    groups: list[dict[str, object]] = []
    oversize: list[dict[str, object]] = []
    for source_group, message in zip(source_groups, ENGLISH_MESSAGES, strict=True):
        source_message = str(source_group["source_message"])
        sources = [source for source in source_group["source_fragments"] if source]
        capacities = [len(source.encode("cp936")) for source in sources]
        prefix = re.match(r"^\[v10\]\[s[45]\]", sources[0])
        if prefix is None:
            raise ValueError(f"missing voice prefix: {sources[0]}")
        english_message = prefix.group(0) + message
        if len(english_message) > sum(capacities):
            oversize.append(
                {
                    "row": source_group["row"],
                    "capacity": sum(capacities),
                    "length": len(english_message),
                    "message": english_message,
                }
            )
            continue
        target_parts = split_text(english_message, capacities)
        group = {
            "source_row": source_group["row"],
            "source_message": source_message,
            "english_message": english_message,
            "source_fragments": sources,
            "target_fragments": target_parts,
            "capacities": capacities,
        }
        groups.append(group)
        for source, target in zip(sources, target_parts, strict=True):
            previous = replacements.get(source)
            if previous is not None and previous != target:
                conflicts.append({"source": source, "first": previous, "second": target})
            replacements[source] = target

    if oversize:
        raise ValueError(json.dumps(oversize, ensure_ascii=False, indent=2))
    if conflicts:
        raise ValueError(f"conflicting repeated fragments: {conflicts}")
    if any(re.search(r"[\u3400-\u9fff]", target) for target in replacements.values()):
        raise ValueError("a target still contains CJK")

    payload = {
        "schema_version": 1,
        "purpose": "Final CommunicationProgram voice comment/string replacement batch",
        "program": bundle["tree"]["english_name"],
        "groups": groups,
        "replacements": [
            {"source": source, "target": target}
            for source, target in sorted(
                replacements.items(), key=lambda pair: len(pair[0]), reverse=True
            )
        ],
        "summary": {
            "message_groups": len(groups),
            "unique_replacements": len(replacements),
            "conflicts": 0,
            "remaining_cjk_targets": 0,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
