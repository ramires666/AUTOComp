"""Typed values for the application-agnostic Windows desktop worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DesktopInputOperation(StrEnum):
    CLICK = "click"
    RIGHT = "right"
    DOUBLE = "double"
    WHEEL = "wheel"
    TYPE_TEXT = "type_text"
    KEY_ENTER = "key_enter"
    KEY_ESCAPE = "key_escape"
    KEY_CTRL_A = "key_ctrl_a"
    KEY_F2 = "key_f2"
    TAB = "tab"
    SHIFT_TAB = "shift_tab"


@dataclass(frozen=True, slots=True)
class DesktopWindow:
    handle: int
    title: str
    process_id: int
    bounds: tuple[int, int, int, int]
    minimized: bool


@dataclass(frozen=True, slots=True)
class DesktopFrame:
    handle: int
    title: str
    process_id: int
    bounds: tuple[int, int, int, int]
    width: int
    height: int
    png_base64: str
    png_sha256: str
    mime_type: str = "image/png"
