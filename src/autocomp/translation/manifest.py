"""Reversible, serializable dry-run manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .models import TranslationDecision


@dataclass(slots=True)
class TranslationManifest:
    checkpoint: str
    dry_run: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    decisions: list[TranslationDecision] = field(default_factory=list)

    def add(self, decision: TranslationDecision) -> None:
        self.decisions.append(decision)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "dry_run": self.dry_run,
            "created_at": self.created_at,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }
