"""One-off batch translation of existing KV STUDIO program properties."""

from __future__ import annotations

import json
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / "scripts" / "batch-kvstudio-english-import.py"))
POST = HELPER["post"]
WINDOWS = HELPER["windows"]
WAIT_WINDOW = HELPER["wait_window"]
MAIN_WINDOW = HELPER["main_window"]
INPUT = HELPER["input_"]
MAIN_HANDLE = HELPER["MAIN_HANDLE"]
PID = HELPER["PID"]
MIRROR = ROOT / "reports" / "12-new-project-english-mirror.json"
STATE = ROOT / ".autocomp" / "direct-program-property-state.json"


def split_target(display: str) -> tuple[str, str | None]:
    if ":" not in display:
        return display, None
    name, comment = display.split(":", 1)
    return name, comment


def sequence(window: dict, checkpoint: str, operations: list[dict]) -> None:
    POST(
        {
            "action": "desktop_input_sequence",
            "window_handle": window["handle"],
            "expected_pid": PID,
            "expected_title": window["title"],
            "checkpoint": checkpoint,
            "operations": operations,
            "apply": True,
        },
        allow_gone=True,
    )


def rename_program(locator: list[int], source: str, target: str, number: int) -> None:
    tag = f"direct-program-property-{number:03d}"
    parent = "Every-scan execution" if locator[1] == 0 else "Standby module"
    POST(
        {
            "action": "activate_tree_item",
            "checkpoint": tag + "-activate",
            "locator": locator,
            "expected_path": ["Program: V3-6-0-8-finall", parent, source],
            "expected_source": source,
            "apply": True,
        }
    )
    main = MAIN_WINDOW()
    INPUT(main, tag + "-program-menu", "click", x=195, y=35)
    menu = WAIT_WINDOW(
        lambda w: not w["title"] and w["owner_handle"] == MAIN_HANDLE
        and 200 <= w["bounds"][2] - w["bounds"][0] <= 300,
        "Program menu",
    )
    INPUT(menu, tag + "-property", "click", x=75, y=115, allow_gone=True)
    dialog = WAIT_WINDOW(
        lambda w: w["title"] == "Program property" and w["enabled"],
        "Program property",
    )
    width = dialog["bounds"][2] - dialog["bounds"][0]
    height = dialog["bounds"][3] - dialog["bounds"][1]
    name, comment = split_target(target)
    if len(name) > 31 or (comment is not None and len(comment) > 31):
        raise RuntimeError(f"field too long for {target!r}")
    operations = [
        {"operation": "click", "x": round(width * 0.646), "y": round(height * 0.118)},
        {"operation": "key_ctrl_a"},
        {"operation": "type_text", "text": name},
    ]
    if comment is not None:
        operations.extend(
            [
                {"operation": "click", "x": round(width * 0.500), "y": round(height * 0.642)},
                {"operation": "key_ctrl_a"},
                {"operation": "type_text", "text": comment},
            ]
        )
    operations.append(
        {"operation": "click", "x": round(width * 0.674), "y": round(height * 0.940)}
    )
    sequence(dialog, tag + "-edit-confirm", operations)
    MAIN_WINDOW()


def run() -> None:
    programs = json.loads(MIRROR.read_text(encoding="utf-8"))["programs"]
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"done": [2]}
    done = set(state.get("done", []))
    # The first original module was replaced by the successful mnemonic pilot.
    # Remaining original every-scan modules shifted left by one slot.
    for number in range(3, 48):
        if number in done:
            continue
        record = programs[number - 1]["tree_map"]["names"]
        source = record["current"]
        target = record["english"]
        if number == 11:
            target = "A_Command_Section_One"
        elif number == 33:
            target = "Aux_Program_Section"
        rename_program([4, 0, number - 2], source, target, number)
        done.add(number)
        STATE.write_text(json.dumps({"done": sorted(done)}, indent=2) + "\n", encoding="utf-8")
        print(f"renamed {number:02d}/48 {source} -> {target}", flush=True)

    if 48 not in done:
        source = programs[47]["tree_map"]["names"]["current"]
        rename_program([4, 2, 0], source, "Update_Log1_EN", 48)
        done.add(48)
        STATE.write_text(json.dumps({"done": sorted(done)}, indent=2) + "\n", encoding="utf-8")
        print(f"renamed 48/48 {source} -> Update_Log1_EN", flush=True)


if __name__ == "__main__":
    run()
