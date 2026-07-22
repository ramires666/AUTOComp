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
    KEY_CTRL_C = "key_ctrl_c"
    KEY_CTRL_D = "key_ctrl_d"
    KEY_CTRL_HOME = "key_ctrl_home"
    KEY_CTRL_SHIFT_END = "key_ctrl_shift_end"
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
    owner_handle: int = 0
    foreground: bool = False
    enabled: bool = True
    class_name: str = ""


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


@dataclass(frozen=True, slots=True)
class DesktopClipboardText:
    """One bounded, read-only CF_UNICODETEXT clipboard snapshot."""

    text: str
    length: int
    utf8_bytes: int
    sha256: str
