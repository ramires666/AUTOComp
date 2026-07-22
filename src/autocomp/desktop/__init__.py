"""Universal, application-agnostic Windows desktop automation."""

from .adapter import UniversalDesktopAdapter
from .models import (
    DesktopClipboardFormat,
    DesktopClipboardSnapshot,
    DesktopClipboardText,
    DesktopFrame,
    DesktopInputOperation,
    DesktopWindow,
)

__all__ = [
    "DesktopClipboardFormat",
    "DesktopClipboardSnapshot",
    "DesktopClipboardText",
    "DesktopFrame",
    "DesktopInputOperation",
    "DesktopWindow",
    "UniversalDesktopAdapter",
]
