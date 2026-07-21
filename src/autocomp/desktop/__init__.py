"""Universal, application-agnostic Windows desktop automation."""

from .adapter import UniversalDesktopAdapter
from .models import DesktopFrame, DesktopInputOperation, DesktopWindow

__all__ = [
    "DesktopFrame",
    "DesktopInputOperation",
    "DesktopWindow",
    "UniversalDesktopAdapter",
]
