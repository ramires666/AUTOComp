import runpy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fast-program-comments.py"
M = runpy.run_path(str(SCRIPT))


def test_program_selection_and_identifier_gate_shape() -> None:
    tree = {
        "project_tree_inventory": {
            "roots": [{"children": []}] * 4
            + [
                {
                    "children": [
                        {
                            "children": [
                                {
                                    "locator": [4, 0, 2],
                                    "name": "A_30号指令:取盘",
                                    "path": ["P", "M", "A_30号指令:取盘"],
                                    "children": [],
                                },
                                {
                                    "locator": [4, 0, 3],
                                    "name": "English: leave",
                                    "path": ["P", "M", "English: leave"],
                                    "children": [],
                                },
                            ]
                        }
                    ]
                }
            ]
        }
    }
    manifest = {
        "decisions": [
            {"source_text": "A_30号指令:取盘", "target_text": "A_30 Command: Pick Up Tray"}
        ]
    }
    item = M["_program_items"](tree, manifest)[0]
    assert item["source_identifier"] == "A_30号指令"
    assert item["target_comment"] == " Pick Up Tray"
    ops = M["_sequence_for_dialog"](item, {"bounds": [0, 0, 400, 250]}, identifier_renames=True)
    assert [op["operation"] for op in ops] == [
        "click",
        "key_ctrl_a",
        "type_text",
        "click",
        "key_ctrl_a",
        "type_text",
    ]
