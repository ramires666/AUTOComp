"""Apply the final CommunicationProgram voice replacements through KV STUDIO."""

from __future__ import annotations

import json
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAPPING = ROOT / ".autocomp" / "final-voice-replacements.json"
STATE = ROOT / ".autocomp" / "final-voice-replace-state.json"
helper = runpy.run_path(str(ROOT / "scripts" / "batch-kvstudio-english-import.py"))


def main() -> None:
    pairs = json.loads(MAPPING.read_text(encoding="utf-8"))["replacements"]
    completed = 0
    if STATE.exists():
        completed = int(json.loads(STATE.read_text(encoding="utf-8")).get("completed", 0))

    for index, pair in enumerate(pairs[completed:], completed + 1):
        search = helper["wait_window"](
            lambda window: window["title"] == "Search" and window["enabled"],
            "enabled Search",
            10,
        )
        helper["post"](
            {
                "action": "desktop_input_sequence",
                "window_handle": search["handle"],
                "expected_pid": search["process_id"],
                "expected_title": search["title"],
                "checkpoint": f"final-voice-replace-{index:03d}",
                "operations": [
                    {"operation": "click", "x": 290, "y": 76, "pause_ms": 40},
                    {"operation": "key_ctrl_a", "pause_ms": 20},
                    {"operation": "type_text", "text": pair["source"], "pause_ms": 30},
                    {"operation": "click", "x": 290, "y": 106, "pause_ms": 40},
                    {"operation": "key_ctrl_a", "pause_ms": 20},
                    {"operation": "type_text", "text": pair["target"], "pause_ms": 30},
                    {"operation": "click", "x": 351, "y": 366, "pause_ms": 80},
                ],
                "apply": True,
            }
        )
        result = helper["wait_window"](
            lambda window: window["title"] == "KV STUDIO"
            and window["enabled"]
            and window["owner_handle"] == search["handle"],
            "replacement result",
            10,
        )
        helper["input_"](
            result,
            f"final-voice-result-{index:03d}",
            "key_enter",
            allow_gone=True,
        )
        STATE.write_text(
            json.dumps(
                {
                    "completed": index,
                    "total": len(pairs),
                    "last_source": pair["source"],
                    "last_target": pair["target"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"{index}/{len(pairs)}", flush=True)


if __name__ == "__main__":
    main()
