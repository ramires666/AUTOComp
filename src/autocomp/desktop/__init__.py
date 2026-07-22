"""Universal, application-agnostic Windows desktop automation."""

from .adapter import UniversalDesktopAdapter
from .models import (
    DesktopClipboardText,
    DesktopFrame,
    DesktopInputOperation,
    DesktopWindow,
)

__all__ = [
    "DesktopClipboardText",
    "DesktopFrame",
    "DesktopInputOperation",
    "DesktopWindow",
    "UniversalDesktopAdapter",
]
