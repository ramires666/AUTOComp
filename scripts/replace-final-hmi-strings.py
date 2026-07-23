"""One-off replacement of the final operator-facing HMI strings."""

import runpy
from pathlib import Path


root = Path(__file__).resolve().parents[1]
d = runpy.run_path(str(root / "scripts" / "batch-kvstudio-english-import.py"))
pairs = [
    ("熔炉温度", "Furnace Temp"),
    ("石墨工位温度", "Graphite Station Temp"),
    ("回水温度", "Return Water Temp"),
    ("真空箱体温度", "Vacuum Chamber Temp"),
    ("设备内温度", "Cabinet Temp"),
    ("设备内湿度", "Cabinet Humidity"),
]

for index, (source, target) in enumerate(pairs, 1):
    search = d["wait_window"](
        lambda w: w["title"] == "Search" and w["enabled"], "Search", 10
    )
    operations = [
        {"operation": "click", "x": 290, "y": 76},
        {"operation": "key_ctrl_a"},
        {"operation": "type_text", "text": source},
        {"operation": "click", "x": 290, "y": 106},
        {"operation": "key_ctrl_a"},
        {"operation": "type_text", "text": target},
        {"operation": "click", "x": 351, "y": 366},
    ]
    d["post"](
        {
            "action": "desktop_input_sequence",
            "window_handle": search["handle"],
            "expected_pid": 15496,
            "expected_title": "Search",
            "checkpoint": f"final-hmi-string-{index}",
            "operations": operations,
            "apply": True,
        }
    )
    result = d["wait_window"](
        lambda w: w["title"] == "KV STUDIO" and w["enabled"]
        and w["owner_handle"] == search["handle"],
        "replacement result",
        10,
    )
    d["input_"](
        result, f"final-hmi-result-{index}", "key_enter", allow_gone=True
    )
    print(f"replaced {index}/6 {source} -> {target}", flush=True)
