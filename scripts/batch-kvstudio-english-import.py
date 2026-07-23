"""One-off audited controller for the confirmed KV STUDIO mnemonic import flow."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env.remote"
STATE = ROOT / ".autocomp" / "english-import-live-state.json"
MIRROR = ROOT / "reports" / "12-new-project-english-mirror.json"
REMOTE_DIR = r"C:\projects\AUTOComp\mnemonic-export\english-cp936"
PID = 15496
MAIN_HANDLE = 1119380

NAMES = (
    "Init_Fix_Z_Axis_Drop", "Main_EN", "Parts_Life",
    "Chiller_Stop_Pump_Delay", "Outputs", "Alarms", "Axis_1_4_Positioning",
    "Dry_Cycle", "IPC_Commands", "A_Command_Flow", "A_Command_Section_1",
    "A01_Tray_To_Fine_Scale", "A50_Tray_Pickup_Gate", "A04_Tare_Open_Gate",
    "A51_Close_Pick_Fine", "A09_Fine_Scale_To_Camera",
    "A30_Pure_Gold_To_Graphite", "A31_K_Gold_To_Graphite",
    "A52_XRF_Graphite_Tray_Ops", "A15_Gold_Melting",
    "A32_Return_Graphite_Home", "A22_Carrier_To_Vision",
    "A23_XRF_To_Fine_Scale", "A24_Fine_Scale_To_Carrier",
    "A25_Tip_Carrier_Tray", "A26_XRF_XY_Single_Step",
    "A28_Tray_Pickup_To_Carrier", "A53_XRF_To_Tray_Pickup",
    "A54_IPC_Command", "A55_Fine_Scale_To_Tray_Pickup",
    "A56_Carrier_To_Tray_Pickup", "A57_Vision_To_Tray_Pickup",
    "Aux_Program_Section", "Communications_1_3_4_9", "Water_Vacuum_Alarms",
    "Air_Tank_Vacuum_20240806", "Alarm_Map_MR_DM102",
    "After_Sales_Point_To_Point", "One_Touch_Program", "KEYENCE_HMI",
    "Preliminary_RD", "New_Chiller_Status_Alarms", "MQTT_4G_Communications",
    "Secondary_Tare", "Port0_FineScale_Comms", "Melting_Cooling_Timers",
    "Gripper_Cylinder_Endurance", "Update_Log1_EN",
)


def env() -> tuple[str, str]:
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"\'')
    return values["AUTOCOMP_WORKER_ENDPOINT"].rstrip("/"), values["AUTOCOMP_WORKER_TOKEN"]


ENDPOINT, TOKEN = env()


def post(payload: dict, *, allow_gone: bool = False) -> dict:
    request = urllib.request.Request(
        ENDPOINT + "/v1/action",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if allow_gone and exc.code == 503:
            return {"performed": True, "transient_closed": True}
        raise


def windows() -> list[dict]:
    return post({"action": "desktop_windows"})["desktop_windows"]


def wait_window(predicate, label: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for window in windows():
            if window["process_id"] == PID and predicate(window):
                return window
        time.sleep(0.1)
    raise RuntimeError(f"timeout waiting for {label}")


def main_window() -> dict:
    return wait_window(lambda w: w["handle"] == MAIN_HANDLE and w["enabled"], "enabled main window")


def input_(window: dict, checkpoint: str, operation: str, *, x=None, y=None, text=None, allow_gone=False):
    payload = {
        "action": "desktop_input", "window_handle": window["handle"],
        "expected_pid": PID, "expected_title": window["title"],
        "checkpoint": checkpoint, "operation": operation, "apply": True,
    }
    if x is not None:
        payload.update(x=x, y=y)
    if text is not None:
        payload["text"] = text
    return post(payload, allow_gone=allow_gone)


def import_module(index: int) -> None:
    tag = f"english-import-{index:03d}"
    program = json.loads(MIRROR.read_text(encoding="utf-8"))["programs"][index - 1]
    source = program["tree_map"]["names"]["current"]
    # Program 1 was already replaced by the confirmed pilot.  Until the
    # originals are deleted as one batch, each remaining source is shifted by
    # exactly one slot and must be active for KV STUDIO's contextual reader.
    locator = [4, 0, index - 2]
    post(
        {
            "action": "activate_tree_item",
            "checkpoint": tag + "-activate-source",
            "locator": locator,
            "expected_path": [
                "Program: V3-6-0-8-finall",
                "Every-scan execution",
                source,
            ],
            "expected_source": source,
            "apply": True,
        }
    )
    main = main_window()
    input_(main, tag + "-file-menu", "click", x=22, y=35)
    menu = wait_window(
        lambda w: not w["title"] and w["owner_handle"] == MAIN_HANDLE
        and 300 <= w["bounds"][2] - w["bounds"][0] <= 350
        and w["bounds"][3] - w["bounds"][1] > 500,
        "File menu",
    )
    input_(menu, tag + "-mnemonics-submenu", "click", x=310, y=329)
    submenu = wait_window(
        lambda w: not w["title"] and w["owner_handle"] == MAIN_HANDLE
        and 100 <= w["bounds"][2] - w["bounds"][0] <= 160
        and 40 <= w["bounds"][3] - w["bounds"][1] <= 70,
        "Mnemonics submenu",
    )
    input_(submenu, tag + "-read", "click", x=60, y=35, allow_gone=True)
    dialog = wait_window(lambda w: w["class_name"] == "#32770" and w["enabled"], "Open dialog")
    filename = f"{REMOTE_DIR}\\{index:03d}-4_{0 if index < 48 else 2}_{index - 1 if index < 48 else 0}.mnm"
    input_(dialog, tag + "-filename-focus", "click", x=330, y=406)
    input_(dialog, tag + "-filename-select", "key_ctrl_a")
    input_(dialog, tag + "-filename", "type_text", text=filename)
    input_(dialog, tag + "-open", "key_enter", allow_gone=True)
    duplicate = wait_window(
        lambda w: w["title"] == "KV STUDIO" and w["enabled"]
        and w["bounds"][2] - w["bounds"][0] < 400,
        "duplicate warning",
    )
    input_(duplicate, tag + "-ack-duplicate", "key_enter", allow_gone=True)
    name_dialog = wait_window(lambda w: w["title"] == "Input program name", "program-name dialog")
    input_(name_dialog, tag + "-name", "type_text", text=NAMES[index - 1])
    input_(name_dialog, tag + "-confirm-name", "key_enter", allow_gone=True)
    type_dialog = wait_window(lambda w: w["title"] == "Select program type", "program-type dialog")
    if index == 48:
        raise RuntimeError("standby module requires explicit type selection")
    input_(type_dialog, tag + "-every-scan", "key_enter", allow_gone=True)
    main_window()


def save_state(imported: list[int], deleted: list[int]) -> None:
    STATE.write_text(
        json.dumps({"imported": imported, "deleted_originals": deleted}, indent=2) + "\n",
        encoding="utf-8",
    )


def run_imports(start: int = 2, stop: int = 47) -> None:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"imported": [1], "deleted_originals": [1]}
    imported = list(state["imported"])
    deleted = list(state["deleted_originals"])
    for index in range(start, stop + 1):
        if index in imported:
            continue
        import_module(index)
        imported.append(index)
        save_state(imported, deleted)
        print(f"imported {index:02d}/47 {NAMES[index - 1]}", flush=True)


if __name__ == "__main__":
    run_imports()
