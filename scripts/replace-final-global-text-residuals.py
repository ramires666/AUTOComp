"""Replace the final non-rung Chinese text found by the global KV audit."""

from __future__ import annotations

import json
import runpy
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
helper = runpy.run_path(str(ROOT / "scripts" / "batch-kvstudio-english-import.py"))

STATIONS = {
    "精秤位": "Fine Weighing Station",
    "取盘位": "Tray-Pickup Station",
    "测金位": "XRF Assay Station",
    "载盘位": "Carrier-Tray Station",
    "石墨位": "Graphite-Crucible Station",
    "熔炼位": "Induction Melting Station",
    "开盖位": "Lid-Opening Station",
    "摆盖位": "Lid-Handling Station",
    "相机位": "Vision Station",
    "K金位": "K-Gold Station",
    "纯金位": "Pure-Gold Station",
}


def replacements() -> list[tuple[str, str]]:
    voice = json.loads(
        (ROOT / ".autocomp" / "final-voice-replacements.json").read_text(
            encoding="utf-8"
        )
    )
    pairs = {
        group["source_message"]: group["english_message"]
        for group in voice["groups"]
    }
    pairs.update(STATIONS)
    return sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)


def main() -> None:
    search = helper["wait_window"](
        lambda window: window["title"] == "Search" and window["enabled"],
        "Search",
        10,
    )
    pairs = replacements()
    for index, (source, target) in enumerate(pairs, 1):
        payload = {
            "action": "desktop_input_sequence",
            "window_handle": search["handle"],
            "expected_pid": search["process_id"],
            "expected_title": search["title"],
            "checkpoint": f"final-global-text-{index:02d}",
            "operations": [
                {"operation": "click", "x": 290, "y": 76, "pause_ms": 20},
                {"operation": "key_ctrl_a", "pause_ms": 10},
                {"operation": "type_text", "text": source, "pause_ms": 20},
                {"operation": "click", "x": 290, "y": 106, "pause_ms": 20},
                {"operation": "key_ctrl_a", "pause_ms": 10},
                {"operation": "type_text", "text": target, "pause_ms": 20},
                {"operation": "click", "x": 351, "y": 366, "pause_ms": 0},
            ],
            "apply": True,
        }
        try:
            helper["post"](payload)
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                raise
        result = helper["wait_window"](
            lambda window: window["title"] == "KV STUDIO"
            and window["enabled"]
            and window["owner_handle"] == search["handle"],
            "replace result",
            10,
        )
        helper["input_"](
            result,
            f"final-global-text-result-{index:02d}",
            "key_escape",
            allow_gone=True,
        )
        print(f"{index}/{len(pairs)}", flush=True)


if __name__ == "__main__":
    main()
