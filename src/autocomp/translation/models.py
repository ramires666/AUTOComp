"""Typed data structures shared by translation stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class TextKind(StrEnum):
    COMMENT = "comment"
    PROGRAM_NAME = "program_name"
    IDENTIFIER = "identifier"
    STRING_LITERAL = "string_literal"
    UI_LABEL = "ui_label"
    SCRIPT_COMMENT = "script_comment"
    OTHER = "other"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    """One user-authored text value extracted from a project export or UI."""

    record_id: str
    source_text: str
    kind: TextKind
    hierarchy: tuple[str, ...] = ()
    context: str = ""
    location: str = ""
    risk: RiskLevel = RiskLevel.LOW
    requires_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind.value
        result["risk"] = self.risk.value
        result["hierarchy"] = list(self.hierarchy)
        return result


@dataclass(frozen=True, slots=True)
class TranslationDecision:
    record_id: str
    source_text: str
    target_text: str | None
    status: str
    risk: RiskLevel
    requires_review: bool
    reason: str = ""
    protected_tokens: tuple[str, ...] = ()
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["risk"] = self.risk.value
        result["protected_tokens"] = list(self.protected_tokens)
        return result


@dataclass(frozen=True, slots=True)
class ProviderTranslation:
    translation: str
    notes: str = ""
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderBatchItem:
    """A protected item sent to an inference provider as part of one request."""

    record_id: str
    text: str
    context: str
