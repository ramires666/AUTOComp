"""Apply the reviewed CJK-to-English rung-comment span dictionary."""

from __future__ import annotations

import json
import re
import runpy
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports" / "11-english-span-translations.json"
STATE = ROOT / ".autocomp" / "all-reviewed-rung-span-state.json"
helper = runpy.run_path(str(ROOT / "scripts" / "batch-kvstudio-english-import.py"))

ALREADY_APPLIED = {
    "播放完成，选择是否继续播放",
    "按语种的设置进行选择性播报",
    "参数填写完成，发送语音指令",
    "模拟上位机给-终检-数据",
    "播报完成，清除和复位",
    "句与句之间的延时",
    "普+英+方",
    "普后延时",
    "英后延时",
    "方后延时",
    "仅方言",
    "普+英",
    "普+方",
    "英+方",
    "仅普",
    "仅英",
    "报警重复播报程序",
    "白色款语音播报",
    "黑色款语音播报",
}


def pairs() -> list[tuple[str, str]]:
    translations = json.loads(SOURCE.read_text(encoding="utf-8"))["translations"]
    result = [
        (source, target)
        for source, target in translations.items()
        if source not in ALREADY_APPLIED
        and source != target
        and re.search(r"[\u3400-\u9fff]", source)
        and not re.search(r"[\u3400-\u9fff]", target)
        and "\n" not in source
        and "\n" not in target
        and len(source) <= 512
        and len(target) <= 512
    ]
    return sorted(result, key=lambda pair: (len(pair[0]), pair[0]), reverse=True)


def main() -> None:
    replacements = pairs()
    completed = 0
    if STATE.exists():
        completed = int(json.loads(STATE.read_text(encoding="utf-8")).get("completed", 0))
    search = helper["wait_window"](
        lambda window: window["title"] == "Search" and window["enabled"],
        "Search",
        10,
    )
    for index, (source, target) in enumerate(
        replacements[completed:], completed + 1
    ):
        payload = {
                "action": "desktop_input_sequence",
                "window_handle": search["handle"],
                "expected_pid": search["process_id"],
                "expected_title": search["title"],
                "checkpoint": f"all-reviewed-rung-span-{index:03d}",
                "operations": [
                    {"operation": "click", "x": 290, "y": 76, "pause_ms": 25},
                    {"operation": "key_ctrl_a", "pause_ms": 15},
                    {"operation": "type_text", "text": source, "pause_ms": 25},
                    {"operation": "click", "x": 290, "y": 106, "pause_ms": 25},
                    {"operation": "key_ctrl_a", "pause_ms": 15},
                    {"operation": "type_text", "text": target, "pause_ms": 25},
                    {"operation": "click", "x": 351, "y": 366, "pause_ms": 0},
                ],
                "apply": True,
            }
        try:
            helper["post"](payload)
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                raise
            # The click may already have opened the result modal.  Verify and
            # close that exact owned dialog below; never repeat the mutation.
            pass
        result = helper["wait_window"](
            lambda window: window["title"] == "KV STUDIO"
            and window["enabled"]
            and window["owner_handle"] == search["handle"],
            "replace result",
            10,
        )
        helper["input_"](
            result,
            f"all-reviewed-rung-result-{index:03d}",
            "key_escape",
            allow_gone=True,
        )
        STATE.write_text(
            json.dumps(
                {
                    "completed": index,
                    "total": len(replacements),
                    "last_source": source,
                    "last_target": target,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if index == 1 or index % 10 == 0 or index == len(replacements):
            print(f"{index}/{len(replacements)}", flush=True)


if __name__ == "__main__":
    main()
