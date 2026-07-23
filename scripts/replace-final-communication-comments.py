"""Replace the remaining CommunicationProgram control comments."""

from __future__ import annotations

import json
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".autocomp" / "final-communication-comment-state.json"
helper = runpy.run_path(str(ROOT / "scripts" / "batch-kvstudio-english-import.py"))

PAIRS = [
    ("播放完成，选择是否继续播放", "Playback Done; Choose Whether to Continue"),
    ("按语种的设置进行选择性播报", "Selective Playback by Language"),
    ("参数填写完成，发送语音指令", "Parameters Ready; Send Voice Command"),
    ("模拟上位机给-终检-数据", "Simulated Host - Final Inspection Data"),
    ("播报完成，清除和复位", "Playback Done; Clear and Reset"),
    ("句与句之间的延时", "Delay Between Sentences"),
    ("普+英+方", "Mandarin+English+Dialect"),
    ("普后延时", "Mandarin Delay"),
    ("英后延时", "English Delay"),
    ("方后延时", "Dialect Delay"),
    ("仅方言", "Dialect Only"),
    ("普+英", "Mandarin+English"),
    ("普+方", "Mandarin+Dialect"),
    ("英+方", "English+Dialect"),
    ("仅普", "Mandarin Only"),
    ("仅英", "English Only"),
]


def main() -> None:
    completed = 0
    if STATE.exists():
        completed = int(json.loads(STATE.read_text(encoding="utf-8")).get("completed", 0))
    for index, (source, target) in enumerate(PAIRS[completed:], completed + 1):
        search = helper["wait_window"](
            lambda window: window["title"] == "Search" and window["enabled"],
            "Search",
            10,
        )
        helper["post"](
            {
                "action": "desktop_input_sequence",
                "window_handle": search["handle"],
                "expected_pid": search["process_id"],
                "expected_title": search["title"],
                "checkpoint": f"final-communication-comment-{index:02d}",
                "operations": [
                    {"operation": "click", "x": 290, "y": 76, "pause_ms": 30},
                    {"operation": "key_ctrl_a", "pause_ms": 20},
                    {"operation": "type_text", "text": source, "pause_ms": 30},
                    {"operation": "click", "x": 290, "y": 106, "pause_ms": 30},
                    {"operation": "key_ctrl_a", "pause_ms": 20},
                    {"operation": "type_text", "text": target, "pause_ms": 30},
                    {"operation": "click", "x": 351, "y": 366, "pause_ms": 80},
                ],
                "apply": True,
            }
        )
        result = helper["wait_window"](
            lambda window: window["title"] == "KV STUDIO"
            and window["enabled"]
            and window["owner_handle"] == search["handle"],
            "replace result",
            10,
        )
        helper["input_"](
            result,
            f"final-communication-result-{index:02d}",
            "key_enter",
            allow_gone=True,
        )
        STATE.write_text(
            json.dumps({"completed": index, "total": len(PAIRS)}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{index}/{len(PAIRS)}", flush=True)


if __name__ == "__main__":
    main()
