from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fast-bookmark-batch.py"
MODULE = runpy.run_path(str(SCRIPT))
find_band = MODULE["_find_unique_comment_band"]
load_items = MODULE["_items"]


def _image_with_bands(*bands: tuple[int, int]) -> Image.Image:
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    for top, bottom in bands:
        draw.rectangle((50, top, 279, bottom), fill=(159, 207, 240))
        draw.rectangle((75, top + 4, 105, bottom - 4), fill="black")
    return image


def test_finds_unique_long_exact_rgb_band() -> None:
    detected_y, band = find_band(
        _image_with_bands((60, 88)),
        left=40,
        right=290,
        top=20,
        bottom=180,
    )

    assert detected_y == 74
    assert band == (60, 88)


@pytest.mark.parametrize("bands", [(), ((40, 60), (100, 120))])
def test_rejects_missing_or_ambiguous_band(bands: tuple[tuple[int, int], ...]) -> None:
    with pytest.raises(ValueError, match="expected one"):
        find_band(
            _image_with_bands(*bands),
            left=40,
            right=290,
            top=20,
            bottom=180,
        )


def test_items_require_source_and_target(tmp_path: Path) -> None:
    path = tmp_path / "items.json"
    path.write_text(
        json.dumps([{"record_id": "one", "tree_y": 100, "target": "Alarm"}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source"):
        load_items(path)

