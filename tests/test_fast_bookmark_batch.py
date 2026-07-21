from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "fast-bookmark-batch.py")
)
find_band = MODULE["_find_unique_comment_band"]
load_items = MODULE["_items"]
edit_operations = MODULE["_edit_operations"]
completed_ids = MODULE["_completed_ids"]
save_completed = MODULE["_save_completed"]


def _image(*bands: tuple[int, int]) -> Image.Image:
    result = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(result)
    for top, bottom in bands:
        draw.rectangle((15, top, 305, bottom), fill=(159, 207, 240))
    return result


def test_finds_one_selected_band() -> None:
    assert find_band(_image((60, 88))) == (74, (60, 88))


@pytest.mark.parametrize("bands", [(), ((40, 60), (100, 120))])
def test_rejects_missing_or_ambiguous_band(bands: tuple[tuple[int, int], ...]) -> None:
    with pytest.raises(ValueError, match="expected one"):
        find_band(_image(*bands))


def test_items_and_edit_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "items.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "record_id": "one",
                        "locator": [4, 1],
                        "expected_path": ["program", "bookmarks"],
                        "expected_source": "/*报警*/",
                        "target": "/*Alarm*/",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_items(path)[0]["locator"] == [4, 1]
    operations = edit_operations(
        {
            "width": 1000,
            "height": 700,
            "window_bounds": [100, 50, 1200, 800],
            "client_bounds": [110, 90, 1190, 780],
        },
        200,
        (195, 205),
        "/*Alarm*/",
    )
    assert operations[0] == {"operation": "double", "x": 510, "y": 240, "pause_ms": 180}
    assert operations[-1]["y"] == 261


def test_progress_round_trip_is_bound_to_items_file(tmp_path: Path) -> None:
    items_path = (tmp_path / "items.json").resolve()
    progress_path = tmp_path / "progress.json"

    save_completed(progress_path, items_path, {"two", "one"})

    assert completed_ids(progress_path, items_path) == {"one", "two"}
    with pytest.raises(ValueError, match="another items list"):
        completed_ids(progress_path, tmp_path / "other.json")
